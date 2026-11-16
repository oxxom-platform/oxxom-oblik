from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from catalog.models import Product, Counterparty, Warehouse
from .models import Document, DocumentLine, DocumentSequence, DocumentEvent


class Conflict(Exception):
    pass


def uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('Оберіть запис зі списку.') from None


def numeric(value, digits, places, minimum):
    if not isinstance(value, str):
        raise ValidationError('Кількість і ціну передавайте десятковими рядками.')
    return forms.DecimalField(max_digits=digits, decimal_places=places, min_value=minimum).clean(value)


def next_number(organization, year):
    # The caller holds the organization lock for the entire mutation.
    sequence, _ = DocumentSequence.objects.get_or_create(organization=organization, year=year)
    sequence.last_number += 1
    sequence.save(update_fields=['last_number'])
    return sequence.last_number


def write_draft(organization, user, data, document=None, action=None):
    """Caller owns transaction and organization lock; mutations are all or nothing."""
    if not isinstance(data, dict):
        raise ValidationError('Очікується об’єкт документа.')
    if document:
        if type(data.get('version')) is not int or data['version'] != document.version:
            raise Conflict('Документ змінено в іншій вкладці. Завантажте актуальну версію.')
        if document.status != 'draft' or not document.is_active:
            raise Conflict('Редагування доступне лише для активної чернетки.')
    date = forms.DateField(input_formats=['%Y-%m-%d']).clean(data.get('date'))
    note = forms.CharField(max_length=2000, required=False).clean(data.get('note', ''))
    counterparty_id = uuid(data.get('counterparty'))
    counterparty = Counterparty.objects.filter(organization=organization, pk=counterparty_id).first()
    if not counterparty or (not counterparty.is_active and (not document or document.counterparty_id != counterparty_id)):
        raise ValidationError('Оберіть активного контрагента цієї організації.')
    warehouse_id = uuid(data['warehouse']) if data.get('warehouse') else None
    warehouse = Warehouse.objects.filter(organization=organization, pk=warehouse_id).first() if warehouse_id else None
    if warehouse_id and (not warehouse or (not warehouse.is_active and (not document or document.warehouse_id != warehouse_id))):
        raise ValidationError('Оберіть активний склад цієї організації.')
    rows = data.get('lines')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValidationError('Документ повинен містити від 1 до 100 рядків.')
    old_lines = {str(line.pk): line for line in document.lines.all()} if document else {}
    used_ids = set()
    lines = []
    for position, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict):
                raise ValidationError('Невірний рядок.')
            previous = None
            if row.get('id'):
                key = str(uuid(row['id']))
                previous = old_lines.get(key)
                if previous is None or key in used_ids:
                    raise ValidationError('Невірний або повторний ідентифікатор рядка.')
                used_ids.add(key)
            product_id = uuid(row.get('product'))
            product = Product.objects.select_related('unit').filter(organization=organization, pk=product_id).first()
            retained = previous and previous.product_id == product_id
            if not product or (not product.is_active and not retained):
                raise ValidationError('Оберіть активну позицію цієї організації.')
            if product.unit.organization_id != organization.pk:
                raise ValidationError('Одиниця позиції належить іншій організації.')
            quantity = numeric(row.get('quantity'), 12, 3, Decimal('0.001'))
            price = numeric(row.get('price'), 14, 2, Decimal('0'))
            line = DocumentLine(id=previous.pk if previous else uuid4(), position=position, product=product,
                product_name=previous.product_name if retained else product.name,
                kind=previous.kind if retained else product.kind,
                unit_name=previous.unit_name if retained else product.unit.name,
                unit_symbol=previous.unit_symbol if retained else product.unit.symbol,
                quantity=quantity, price=price, amount=(quantity * price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
            lines.append(line)
        except ValidationError as exc:
            raise ValidationError(f'Рядок {position}: ' + ' '.join(exc.messages)) from None
    if any(line.kind == 'goods' for line in lines) and warehouse is None:
        raise ValidationError('Для товарів оберіть склад.')
    counterparty_snapshot = document.counterparty_snapshot if document and document.counterparty_id == counterparty_id else {field: getattr(counterparty, field) for field in ['name', 'kind', 'code', 'email', 'phone', 'address', 'bank_details']}
    warehouse_name = document.warehouse_name if document and document.warehouse_id == warehouse_id else (warehouse.name if warehouse else '')
    new = document is None
    if new:
        document = Document(organization=organization)
    if new or document.year != date.year:
        document.year = date.year
        document.number = next_number(organization, date.year)
    from printing.profiles import snapshot
    document.issuer_snapshot = snapshot(organization)
    document.date = date
    document.note = note
    document.counterparty = counterparty
    document.counterparty_snapshot = counterparty_snapshot
    document.warehouse = warehouse
    document.warehouse_name = warehouse_name
    document.total = sum((line.amount for line in lines), Decimal('0.00'))
    if not new:
        document.version += 1
    document.updated_at = timezone.now()
    document.save()
    document.lines.all().delete()
    for line in lines:
        line.document = document
    DocumentLine.objects.bulk_create(lines)
    DocumentEvent.objects.create(organization=organization, actor=user, document=document, action=action or ('create' if new else 'update'), version=document.version)
    return document
