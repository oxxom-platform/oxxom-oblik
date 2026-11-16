from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP, localcontext
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from documents.models import DocumentEvent
from inventory.models import StockEvent
from documents.services import Conflict
from .models import Balance, Posting, Movement, Period

ZERO = Decimal('0.00')


def cents(value):
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def open_date(organization, date):
    closed = Period.objects.filter(organization=organization).values_list('closed_through', flat=True).first()
    if closed and date <= closed:
        raise Conflict('Період закритий. Змініть дату або зверніться до власника.')
    if date > timezone.localdate():
        raise ValidationError('Проведення майбутньою датою поки не підтримується.')


def balance_for(organization, document, product):
    balance, _ = Balance.objects.get_or_create(organization=organization, warehouse=document.warehouse, product=product, defaults={'unit':product.unit})
    if balance.unit_id != product.unit_id:
        raise Conflict('Одиниця товару змінена. Створіть окрему картку для іншої одиниці.')
    if balance.last_date and document.date < balance.last_date:
        raise Conflict('Задня дата змінює наступні складські рухи. Проведіть документ поточною датою.')
    return balance


def persist_balance(balance):
    if balance.quantity < 0 or balance.value < 0 or (not balance.quantity and balance.value):
        raise Conflict('Операція створює некоректний залишок.')
    balance.full_clean()
    balance.save()


def move(posting, document, line, quantity, value, date=None, reversal=False):
    return Movement.objects.create(posting=posting, organization=posting.organization, warehouse=document.warehouse,
        product=line.product, line_id=line.pk, product_name=line.product_name, unit_symbol=line.unit_symbol,
        warehouse_name=document.warehouse_name, date=date or document.date, quantity=quantity, value=value, is_reversal=reversal)


def record_status(document, user, status, action):
    document.status = status
    document.version += 1
    document.updated_at = timezone.now()
    document.save(update_fields=['status','version','updated_at'])
    if document._meta.app_label == 'operations':
        from operations.models import OperationEvent
        event = OperationEvent
    else:
        event = DocumentEvent if document._meta.app_label == 'documents' else StockEvent
    event.objects.create(organization=document.organization, actor=user, document=document, action=action, version=document.version)


def post_document(organization, user, document, source):
    if source == 'operation':
        from operations.services import post_operation
        return post_operation(organization,user,document)
    if document.status != 'draft' or not document.is_active:
        raise Conflict('Провести можна тільки активну чернетку. Повторне проведення не створює рухів.')
    open_date(organization, document.date)
    if source == 'sale':
        from printing.profiles import snapshot
        document.issuer_snapshot = snapshot(organization) or document.issuer_snapshot
        document.save(update_fields=['issuer_snapshot'])
    lines = list(document.lines.select_related('product__unit').all())
    if not 1 <= len(lines) <= 100:
        raise ValidationError('Документ має містити від 1 до 100 рядків.')
    if document.warehouse_id and (document.warehouse.organization_id != organization.pk or not document.warehouse.is_active):
        raise ValidationError('Відновіть склад або оберіть активний склад своєї організації.')
    if source == 'sale' and (not document.counterparty.is_active or document.counterparty.organization_id != organization.pk):
        raise ValidationError('Оберіть активного контрагента своєї організації.')
    if source == 'stock' and document.kind == 'receipt' and (not document.counterparty_id or not document.counterparty.is_active or document.counterparty.organization_id != organization.pk):
        raise ValidationError('Для проведення надходження потрібен активний постачальник.')
    grouped = defaultdict(list)
    stock_total = ZERO
    sale_total = ZERO
    for line in lines:
        product = line.product
        if product.organization_id != organization.pk or product.unit.organization_id != organization.pk or not product.is_active:
            raise ValidationError('У документі є чужа або архівна позиція. Відновіть її в довіднику.')
        if line.quantity <= 0 or line.price is None or line.price < 0:
            raise ValidationError('Заповніть додатну кількість та невід’ємну ціну в усіх рядках.')
        if line.amount != cents(line.quantity * line.price):
            raise Conflict('Розрахунки рядків змінилися. Перезбережіть чернетку.')
        if source == 'sale':
            if line.kind != product.kind:
                raise Conflict('Тип позиції змінено. Оновіть позицію в чернетці.')
            sale_total += line.amount
        elif product.kind != 'goods':
            raise ValidationError('Послуги не створюють складських рухів.')
        if product.kind == 'goods':
            if not document.warehouse_id:
                raise ValidationError('Для товарів потрібен склад.')
            grouped[product.pk].append(line)
            stock_total += line.amount
    if (sale_total if source == 'sale' else stock_total) != document.total:
        raise Conflict('Сума документа змінилася. Перезбережіть чернетку.')
    posting = Posting.objects.create(organization=organization, created_by=user, **{source:document})
    total_cost = ZERO
    for product_id in sorted(grouped, key=str):
        rows = grouped[product_id]
        product = rows[0].product
        balance = balance_for(organization, document, product)
        quantity = sum((row.quantity for row in rows), Decimal('0.000'))
        if source == 'stock':
            if document.kind == 'opening' and Movement.objects.filter(organization=organization, warehouse=document.warehouse, product=product, posting__is_reversed=False).exists():
                raise Conflict('Початкові залишки вводяться до інших рухів товару на складі.')
            value = sum((row.amount for row in rows), ZERO)
            for row in rows:
                move(posting, document, row, row.quantity, row.amount)
            balance.quantity += quantity
            balance.value += value
            total_cost += value
        else:
            if balance.quantity < quantity:
                raise Conflict(f'Недостатній залишок: {rows[0].product_name}. Доступно {balance.quantity}, потрібно {quantity}.')
            with localcontext() as context:
                context.prec = 50
                value = balance.value if quantity == balance.quantity else cents(balance.value * quantity / balance.quantity)
                remaining_cost, remaining_quantity = value, quantity
                for row in rows:
                    cost = remaining_cost if row.quantity == remaining_quantity else cents(remaining_cost * row.quantity / remaining_quantity)
                    move(posting, document, row, -row.quantity, -cost)
                    remaining_cost -= cost
                    remaining_quantity -= row.quantity
            balance.quantity -= quantity
            balance.value -= value
            total_cost += value
        balance.last_date = document.date
        persist_balance(balance)
    posting.cost_total = forms.DecimalField(max_digits=25, decimal_places=2, min_value=0).clean(total_cost)
    posting.save(update_fields=['cost_total'])
    record_status(document, user, 'posted', 'post')
    return posting


def reverse_document(organization, user, document, source):
    if document.status != 'posted':
        raise Conflict('Сторнувати можна тільки проведений документ.')
    if source in ('sale','stock'):
        from payments.models import Allocation
        field = 'sale' if source == 'sale' else 'receipt'
        if Allocation.objects.filter(**{field:document},payment__status='posted').exists():
            raise Conflict('Є розподілені оплати. Спочатку зніміть розподіл або сторнуйте платіж у розділі оплат.')
    today = timezone.localdate()
    open_date(organization, today)
    posting = Posting.objects.get(organization=organization, **{source:document})
    if posting.is_reversed:
        raise Conflict('Документ уже сторновано.')
    originals = list(posting.movements.filter(is_reversal=False).select_related('product__unit').order_by('id'))
    groups = defaultdict(list)
    for movement in originals:
        groups[(movement.warehouse_id,movement.product_id)].append(movement)
    for (warehouse_id,product_id), movements in groups.items():
        last_id = max(m.id for m in movements)
        if Movement.objects.filter(organization=organization, warehouse_id=warehouse_id, product_id=product_id, id__gt=last_id, posting__is_reversed=False).exclude(posting=posting).exists():
            raise Conflict('Є наступні рухи товару. Спочатку сторнуйте пізніші документи.')
        balance = Balance.objects.get(organization=organization, warehouse_id=warehouse_id, product_id=product_id)
        if balance.last_date and today < balance.last_date:
            raise Conflict('Сторно не може передувати останньому руху.')
        balance.quantity -= sum((m.quantity for m in movements), Decimal('0.000'))
        balance.value -= sum((m.value for m in movements), ZERO)
        balance.last_date = today
        persist_balance(balance)
        for movement in movements:
            Movement.objects.create(posting=posting, organization=organization, warehouse_id=warehouse_id, product_id=product_id,
                line_id=movement.line_id, product_name=movement.product_name, unit_symbol=movement.unit_symbol,
                warehouse_name=movement.warehouse_name, date=today, quantity=-movement.quantity, value=-movement.value, is_reversal=True)
    posting.is_reversed = True
    posting.reversed_by, posting.reversed_at = user, timezone.now()
    posting.save(update_fields=['is_reversed','reversed_by','reversed_at'])
    record_status(document, user, 'reversed', 'reverse')
    return posting
