from collections import defaultdict
from decimal import Decimal, localcontext
from types import SimpleNamespace
from uuid import uuid4
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone
from catalog.models import Product,Warehouse
from documents.models import Document
from documents.services import uuid,numeric,Conflict
from posting.models import Posting,Movement
from posting.services import balance_for,persist_balance,open_date,cents,record_status,ZERO
from .models import Operation,OperationLine,OperationSequence,OperationEvent


def write_draft(organization,user,data,document=None):
    if document:
        if type(data.get('version')) is not int or data['version']!=document.version:
            raise Conflict('Документ змінено. Оновіть його.')
        if document.status!='draft' or not document.is_active:
            raise Conflict('Редагується лише активна чернетка.')
    kind=data.get('kind')
    if kind not in Operation.Kind.values:
        raise ValidationError('Оберіть тип операції.')
    date=forms.DateField(input_formats=['%Y-%m-%d']).clean(data.get('date'))
    sale=None
    if kind=='return':
        sale=Document.objects.filter(pk=uuid(data.get('source_sale')),organization=organization,status='posted').select_related('warehouse').first()
        if not sale or not sale.warehouse_id:
            raise ValidationError('Оберіть проведений товарний продаж своєї організації.')
        if date<sale.date:
            raise ValidationError('Повернення не може передувати продажу.')
        warehouse=sale.warehouse
    else:
        warehouse=Warehouse.objects.filter(pk=uuid(data.get('warehouse')),organization=organization).first()
    if not warehouse or (not warehouse.is_active and (not document or document.warehouse_id!=warehouse.pk)):
        raise ValidationError('Оберіть активний склад своєї організації.')
    target=None
    if kind=='transfer':
        target=Warehouse.objects.filter(pk=uuid(data.get('target_warehouse')),organization=organization).first()
        if not target or not target.is_active or target.pk==warehouse.pk:
            raise ValidationError('Оберіть інший активний склад цієї організації.')
    rows=data.get('lines')
    if not isinstance(rows,list) or not 1<=len(rows)<=100:
        raise ValidationError('Потрібно від 1 до 100 рядків.')
    previous={str(row.pk):row for row in document.lines.all()} if document else {}
    used=set();origins=set();lines=[]
    for position,row in enumerate(rows,1):
        try:
            if not isinstance(row,dict):
                raise ValidationError('Невірний рядок.')
            old=None
            if row.get('id'):
                key=str(uuid(row['id']));old=previous.get(key)
                if old is None or key in used:
                    raise ValidationError('Чужий або повторний ідентифікатор рядка.')
                used.add(key)
            origin=None
            if sale:
                origin=sale.lines.select_related('product__unit').filter(pk=uuid(row.get('source_line')),kind='goods').first()
                if not origin or origin.pk in origins:
                    raise ValidationError('Оберіть неповторний товарний рядок вихідного продажу.')
                origins.add(origin.pk);product=origin.product
                if row.get('product') and uuid(row['product'])!=product.pk:
                    raise ValidationError('Товар не відповідає рядку продажу.')
            else:
                product=Product.objects.select_related('unit').filter(pk=uuid(row.get('product')),organization=organization,kind='goods').first()
            retained=old and old.product_id==(product.pk if product else None)
            if not product or product.organization_id!=organization.pk or product.unit.organization_id!=organization.pk or (not product.is_active and not retained and not origin):
                raise ValidationError('Оберіть товар своєї організації.')
            quantity=numeric(row.get('quantity'),12,3,Decimal('0.001'))
            if origin and quantity>origin.quantity:
                raise ValidationError('Кількість перевищує продану в цьому рядку.')
            lines.append(OperationLine(id=old.pk if old else uuid4(),position=position,product=product,source_line=origin,
                product_name=origin.product_name if origin else old.product_name if retained else product.name,
                unit_name=origin.unit_name if origin else old.unit_name if retained else product.unit.name,
                unit_symbol=origin.unit_symbol if origin else old.unit_symbol if retained else product.unit.symbol,quantity=quantity))
        except ValidationError as exc:
            raise ValidationError(f'Рядок {position}: '+' '.join(exc.messages)) from None
    new=document is None
    wh_name=sale.warehouse_name if sale else document.warehouse_name if document and document.warehouse_id==warehouse.pk else warehouse.name
    if new:document=Operation(organization=organization)
    if new or document.kind!=kind or document.year!=date.year:
        sequence,_=OperationSequence.objects.get_or_create(organization=organization,kind=kind,year=date.year)
        sequence.last_number+=1;sequence.save(update_fields=['last_number']);document.number=sequence.last_number
    document.kind,document.year,document.date=kind,date.year,date
    document.warehouse,document.warehouse_name=warehouse,wh_name
    document.target_warehouse,document.target_name=target,target.name if target else ''
    document.source_sale,document.source_number=sale,sale.display_number if sale else ''
    document.note=forms.CharField(max_length=2000,required=False).clean(data.get('note',''))
    document.total=None
    if not new:document.version+=1
    document.updated_at=timezone.now();document.save();document.lines.all().delete()
    for row in lines:row.document=document
    OperationLine.objects.bulk_create(lines)
    OperationEvent.objects.create(organization=organization,actor=user,document=document,action='create' if new else 'update',version=document.version)
    return document


def make_movement(posting,doc,line,warehouse,name,quantity,value):
    Movement.objects.create(posting=posting,organization=doc.organization,warehouse=warehouse,product=line.product,line_id=line.pk,
        product_name=line.product_name,unit_symbol=line.unit_symbol,warehouse_name=name,date=doc.date,quantity=quantity,value=value)


def post_operation(organization,user,document):
    if document.status!='draft' or not document.is_active:
        raise Conflict('Провести можна лише активну чернетку.')
    open_date(organization,document.date)
    if not document.warehouse.is_active or document.warehouse.organization_id!=organization.pk:
        raise ValidationError('Склад архівний або належить іншій організації.')
    if document.kind=='transfer' and (not document.target_warehouse_id or not document.target_warehouse.is_active or document.target_warehouse.organization_id!=organization.pk or document.target_warehouse_id==document.warehouse_id):
        raise ValidationError('Оберіть інший активний склад цієї організації.')
    if document.kind=='return' and (not document.source_sale_id or document.source_sale.organization_id!=organization.pk or document.source_sale.status!='posted' or document.source_sale.warehouse_id!=document.warehouse_id or document.date<document.source_sale.date):
        raise Conflict('Вихідний продаж недоступний або сторнований.')
    lines=list(document.lines.select_related('product__unit','source_line').all())
    if not 1<=len(lines)<=100:raise ValidationError('Невірна кількість рядків.')
    grouped=defaultdict(list)
    for row in lines:
        if row.quantity<=0 or row.product.organization_id!=organization.pk or row.product.kind!='goods' or row.product.unit.organization_id!=organization.pk:
            raise ValidationError('Невірний товарний рядок.')
        grouped[row.product_id].append(row)
    posting=Posting.objects.create(organization=organization,operation=document,created_by=user)
    total=ZERO
    for product_id in sorted(grouped,key=str):
        rows=grouped[product_id];product=rows[0].product
        balance=balance_for(organization,document,product)
        quantity=sum((row.quantity for row in rows),Decimal('0.000'))
        with localcontext() as context:
            context.prec=50
            if document.kind=='return':
                value=ZERO;seen=set()
                for row in rows:
                    origin=row.source_line
                    if not origin or origin.document_id!=document.source_sale_id or origin.product_id!=row.product_id or origin.kind!='goods' or origin.pk in seen:
                        raise ValidationError('Рядок не належить вихідному продажу або повторюється.')
                    seen.add(origin.pk)
                    original=Movement.objects.filter(posting__sale_id=document.source_sale_id,line_id=origin.pk,is_reversal=False).first()
                    if not original or original.quantity!=-origin.quantity:
                        raise Conflict('Немає вихідного складського руху для цього рядка.')
                    previous=OperationLine.objects.filter(document__kind='return',document__status='posted',source_line=origin).aggregate(quantity=Sum('quantity'),cost=Sum('cost'))
                    remaining_quantity=origin.quantity-(previous['quantity'] or Decimal('0'))
                    remaining_cost=cents(-original.value-(previous['cost'] or ZERO))
                    if row.quantity>remaining_quantity or remaining_cost<0:
                        raise Conflict('Вже повернуто частину товару. Кількість перевищує доступну для повернення.')
                    row.cost=remaining_cost if row.quantity==remaining_quantity else cents(remaining_cost*row.quantity/remaining_quantity)
                    row.save(update_fields=['cost']);value+=row.cost
                    make_movement(posting,document,row,document.warehouse,document.warehouse_name,row.quantity,row.cost)
                balance.quantity+=quantity;balance.value+=value
            else:
                if not product.is_active:raise ValidationError('Відновіть товар у довіднику перед проведенням.')
                if quantity>balance.quantity:raise Conflict(f'Недостатньо товару: {rows[0].product_name}.')
                value=balance.value if quantity==balance.quantity else cents(balance.value*quantity/balance.quantity)
                remaining_quantity,remaining_cost=quantity,value
                for row in rows:
                    row.cost=remaining_cost if row.quantity==remaining_quantity else cents(remaining_cost*row.quantity/remaining_quantity)
                    remaining_quantity-=row.quantity;remaining_cost-=row.cost;row.save(update_fields=['cost'])
                    make_movement(posting,document,row,document.warehouse,document.warehouse_name,-row.quantity,-row.cost)
                    if document.kind=='transfer':make_movement(posting,document,row,document.target_warehouse,document.target_name,row.quantity,row.cost)
                balance.quantity-=quantity;balance.value-=value
                if document.kind=='transfer':
                    destination=balance_for(organization,SimpleNamespace(warehouse=document.target_warehouse,date=document.date),product)
                    destination.quantity+=quantity;destination.value+=value;destination.last_date=document.date;persist_balance(destination)
        balance.last_date=document.date;persist_balance(balance);total+=value
    posting.cost_total=forms.DecimalField(max_digits=25,decimal_places=2,min_value=0).clean(total);posting.save(update_fields=['cost_total'])
    document.total=posting.cost_total;document.save(update_fields=['total'])
    record_status(document,user,'posted','post')
    return posting
