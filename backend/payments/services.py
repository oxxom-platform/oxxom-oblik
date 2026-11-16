from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone
from catalog.models import Counterparty
from documents.models import Document
from documents.services import Conflict, uuid, numeric
from inventory.models import StockDraft
from posting.services import cents, open_date
from .models import MoneyAccount, Payment, PaymentSequence, Allocation, MoneyMovement, PaymentEvent

ZERO = Decimal('0.00')


def total(query, field='amount'):
    return cents(query.aggregate(value=Sum(field))['value'] or ZERO)


def balance(account):
    return total(MoneyMovement.objects.filter(account=account))


def event(org, user, obj, action, details=None):
    PaymentEvent.objects.create(organization=org, actor=user, object_id=obj.pk, action=action, details=details or {'version': obj.version})


def check_version(obj, data):
    if type(data.get('version')) is not int or data['version'] != obj.version:
        raise Conflict('Запис змінено в іншій вкладці. Відкрийте актуальну версію.')


def allocations_data(payment):
    return [{'source': 'sale' if a.sale_id else 'receipt', 'document': str(a.sale_id or a.receipt_id), 'amount': str(a.amount)} for a in payment.allocations.all()]


def settlement(document):
    field = 'sale' if isinstance(document, Document) else 'receipt'
    paid = total(Allocation.objects.filter(**{field: document}, payment__status='posted', payment__organization=document.organization))
    due = cents(document.total - paid)
    return {'total': str(document.total), 'paid': str(paid), 'due': str(due), 'payment_status': 'paid' if due == 0 else ('partial' if paid else 'unpaid')}


def validate_allocations(org, payment, rows):
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValidationError('Розподіл містить від 0 до 100 документів.')
    allocations, seen, allocated = [], set(), ZERO
    expected = 'sale' if payment.direction == 'in' else 'receipt'
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != {'source','document','amount'} or row.get('source') != expected:
            raise ValidationError(f'Розподіл {index}: надходження оплачують продажі, виплати — надходження товару.')
        key = uuid(row['document'])
        if key in seen:
            raise ValidationError('Документ повторюється в розподілі.')
        seen.add(key)
        model = Document if expected == 'sale' else StockDraft
        query = model.objects.filter(organization=org, pk=key, counterparty=payment.counterparty, status='posted', is_active=True)
        if expected == 'receipt': query = query.filter(kind='receipt')
        document = query.first()
        if not document or document.total is None:
            raise ValidationError('Оберіть проведений документ цього контрагента та організації.')
        amount = numeric(row['amount'], 20, 2, Decimal('0.01'))
        used = total(Allocation.objects.filter(**{expected: document}, payment__status='posted').exclude(payment=payment))
        if amount + used > document.total:
            raise Conflict(f'{document.display_number}: розподіл перевищує неоплачену суму {cents(document.total-used)} грн.')
        allocations.append(Allocation(payment=payment, **{expected: document}, amount=amount))
        allocated += amount
    if allocated > payment.amount:
        raise ValidationError('Розподілено більше, ніж сума платежу.')
    return allocations


def replace_allocations(org, user, payment, data):
    check_version(payment, data)
    if payment.status != 'posted':
        raise Conflict('Розподіл окремо змінюється лише у проведеного платежу.')
    open_date(org, payment.date)
    open_date(org, timezone.localdate())
    before = allocations_data(payment)
    rows = validate_allocations(org, payment, data.get('allocations'))
    payment.allocations.all().delete()
    Allocation.objects.bulk_create(rows)
    payment.version += 1
    payment.updated_at = timezone.now()
    payment.save(update_fields=['version','updated_at'])
    event(org,user,payment,'allocate',{'before':before,'after':allocations_data(payment),'version':payment.version})


def write_draft(org, user, data, payment=None):
    """Caller holds an organization lock in a transaction for every write."""
    if not isinstance(data, dict): raise ValidationError('Очікується об’єкт платежу.')
    if payment:
        check_version(payment,data)
        if payment.status != 'draft' or not payment.is_active:
            raise Conflict('Змінювати можна лише активну чернетку платежу.')
    direction = data.get('direction')
    if direction not in ('in','out'): raise ValidationError('Оберіть напрямок платежу.')
    date = forms.DateField(input_formats=['%Y-%m-%d']).clean(data.get('date'))
    account = MoneyAccount.objects.filter(organization=org, pk=uuid(data.get('account'))).first()
    cp = Counterparty.objects.filter(organization=org, pk=uuid(data.get('counterparty'))).first()
    if not account or (not account.is_active and (not payment or payment.account_id != account.pk)):
        raise ValidationError('Оберіть активну касу або рахунок своєї організації.')
    if not cp or (not cp.is_active and (not payment or payment.counterparty_id != cp.pk)):
        raise ValidationError('Оберіть активного контрагента своєї організації.')
    amount = numeric(data.get('amount'),20,2,Decimal('0.01'))
    purpose = forms.CharField(max_length=2000).clean(data.get('purpose'))
    new = payment is None
    payment = payment or Payment(organization=org)
    if payment.account_id != account.pk:
        payment.account_name, payment.account_details = account.name, account.details
    if payment.counterparty_id != cp.pk:
        payment.counterparty_snapshot = {field:getattr(cp,field) for field in ('name','kind','code','bank_details')}
    payment.account, payment.counterparty = account, cp
    payment.date, payment.direction, payment.amount, payment.purpose = date, direction, amount, purpose
    # Validate before saving; drafts do not reserve the unpaid part of a document.
    rows = validate_allocations(org,payment,data.get('allocations',[]))
    if new or payment.year != date.year:
        seq,_ = PaymentSequence.objects.get_or_create(organization=org,year=date.year)
        seq.last_number += 1; seq.save(update_fields=['last_number'])
        payment.year, payment.number = date.year, seq.last_number
    if not new: payment.version += 1
    payment.updated_at = timezone.now()
    payment.save()
    payment.allocations.all().delete()
    Allocation.objects.bulk_create(rows)
    event(org,user,payment,'create' if new else 'save',{'version':payment.version,'amount':str(amount),'allocations':allocations_data(payment)})
    return payment


def set_status(org,user,payment,status):
    payment.status = status
    payment.version += 1
    payment.updated_at = timezone.now()
    payment.save(update_fields=['status','version','updated_at'])
    event(org,user,payment,status,{'version':payment.version,'amount':str(payment.amount),'account':str(payment.account_id),'allocations':allocations_data(payment)})


def post_payment(org,user,payment):
    if payment.status != 'draft' or not payment.is_active: raise Conflict('Провести можна лише активну чернетку.')
    open_date(org,payment.date)
    if not payment.account.is_active or not payment.counterparty.is_active:
        raise ValidationError('Відновіть рахунок і контрагента в довідниках перед проведенням.')
    validate_allocations(org,payment,allocations_data(payment))
    last = MoneyMovement.objects.filter(account=payment.account).order_by('-date').first()
    if last and payment.date < last.date:
        raise Conflict('Дата передує останньому руху цього рахунку. Оберіть поточну дату.')
    signed = payment.amount if payment.direction == 'in' else -payment.amount
    if balance(payment.account) + signed < ZERO:
        raise Conflict('Недостатньо коштів на рахунку для виплати.')
    MoneyMovement.objects.create(organization=org,payment=payment,account=payment.account,account_name=payment.account_name,date=payment.date,amount=signed)
    set_status(org,user,payment,'posted')


def reverse_payment(org,user,payment):
    if payment.status != 'posted': raise Conflict('Сторнувати можна лише проведений платіж.')
    today = timezone.localdate(); open_date(org,today)
    original = payment.movements.get(is_reversal=False)
    last = MoneyMovement.objects.filter(account=payment.account).order_by('-date').first()
    if last and today < last.date: raise Conflict('Дата сторно передує останньому руху рахунку.')
    if balance(payment.account) - original.amount < ZERO: raise Conflict('Сторно створює від’ємний залишок коштів.')
    MoneyMovement.objects.create(organization=org,payment=payment,account=payment.account,account_name=payment.account_name,date=today,amount=-original.amount,is_reversal=True)
    set_status(org,user,payment,'reversed')
