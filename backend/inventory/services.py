from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from django import forms
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone
from catalog.models import Warehouse, Counterparty, Product
from documents.services import uuid, numeric, Conflict
from .models import StockDraft, StockSequence, StockLine, StockEvent


def write_draft(organization, user, data, document=None, show_cost=False, action=None):
    # Caller holds organization lock and transaction, including audit and numbering.
    if document:
        if type(data.get('version')) is not int or data['version'] != document.version:
            raise Conflict('Чернетку змінено. Завантажте актуальну версію.')
        if document.status != 'draft' or not document.is_active:
            raise Conflict('Редагується лише активна чернетка.')
    kind = data.get('kind')
    if kind not in StockDraft.Kind.values:
        raise ValidationError('Оберіть тип складського документа.')
    date = forms.DateField(input_formats=['%Y-%m-%d']).clean(data.get('date'))
    warehouse_id = uuid(data.get('warehouse'))
    warehouse = Warehouse.objects.filter(pk=warehouse_id, organization=organization).first()
    if not warehouse or (not warehouse.is_active and (not document or document.warehouse_id != warehouse_id)):
        raise ValidationError('Оберіть активний склад своєї організації.')
    if not show_cost and data.get('counterparty'):
        raise PermissionDenied
    supplier_id = uuid(data['counterparty']) if data.get('counterparty') else None
    if not show_cost and document and kind == 'receipt':
        supplier_id = document.counterparty_id
    if kind == 'opening' and supplier_id:
        raise ValidationError('Для початкових залишків постачальник не вказується.')
    supplier = Counterparty.objects.filter(pk=supplier_id, organization=organization).first() if supplier_id else None
    if supplier_id and (not supplier or (not supplier.is_active and (not document or document.counterparty_id != supplier_id))):
        raise ValidationError('Оберіть активного постачальника своєї організації.')
    rows = data.get('lines')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValidationError('Потрібно від 1 до 100 товарних рядків.')
    old = {str(row.pk): row for row in document.lines.all()} if document else {}
    used = set()
    lines = []
    for position, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict):
                raise ValidationError('Невірний рядок.')
            previous = None
            if row.get('id'):
                key = str(uuid(row['id']))
                previous = old.get(key)
                if previous is None or key in used:
                    raise ValidationError('Чужий або повторний ідентифікатор рядка.')
                used.add(key)
            product_id = uuid(row.get('product'))
            product = Product.objects.select_related('unit').filter(pk=product_id, organization=organization).first()
            retained = previous and previous.product_id == product_id
            if not product or product.kind != 'goods' or (not product.is_active and not retained):
                raise ValidationError('Оберіть товар своєї організації. Послуги не обліковуються на складі.')
            if product.unit.organization_id != organization.pk:
                raise ValidationError('Одиниця товару належить іншій організації.')
            quantity = numeric(row.get('quantity'), 12, 3, Decimal('0.001'))
            if show_cost:
                price = numeric(row['price'], 14, 2, Decimal('0')) if row.get('price') not in (None, '') else None
            else:
                if 'price' in row:
                    raise PermissionDenied
                price = previous.price if retained else None
            amount = (quantity * price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if price is not None else None
            lines.append(StockLine(id=previous.pk if previous else uuid4(), position=position, product=product,
                product_name=previous.product_name if retained else product.name,
                unit_name=previous.unit_name if retained else product.unit.name,
                unit_symbol=previous.unit_symbol if retained else product.unit.symbol,
                quantity=quantity, price=price, amount=amount))
        except ValidationError as exc:
            raise ValidationError(f'Рядок {position}: ' + ' '.join(exc.messages)) from None
    new = document is None
    warehouse_name = document.warehouse_name if document and document.warehouse_id == warehouse_id else warehouse.name
    snapshot = document.counterparty_snapshot if document and document.counterparty_id == supplier_id else ({field: getattr(supplier, field) for field in ['name', 'kind', 'code', 'email', 'phone', 'address', 'bank_details']} if supplier else {})
    if new:
        document = StockDraft(organization=organization)
    if new or document.year != date.year or document.kind != kind:
        sequence, _ = StockSequence.objects.get_or_create(organization=organization, kind=kind, year=date.year)
        sequence.last_number += 1
        sequence.save(update_fields=['last_number'])
        document.number = sequence.last_number
    document.kind, document.year, document.date = kind, date.year, date
    document.warehouse, document.warehouse_name = warehouse, warehouse_name
    document.counterparty, document.counterparty_snapshot = supplier, snapshot
    document.note = forms.CharField(max_length=2000, required=False).clean(data.get('note', ''))
    document.total = sum((row.amount for row in lines), Decimal('0.00')) if all(row.amount is not None for row in lines) else None
    if not new:
        document.version += 1
    document.updated_at = timezone.now()
    document.save()
    document.lines.all().delete()
    for row in lines:
        row.document = document
    StockLine.objects.bulk_create(lines)
    StockEvent.objects.create(organization=organization, actor=user, document=document, action=action or ('create' if new else 'update'), version=document.version)
    return document
