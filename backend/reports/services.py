from decimal import Decimal, ROUND_HALF_UP, localcontext
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Sum, OuterRef, Subquery, DecimalField, Value, F, Q
from django.db.models.functions import Coalesce
from catalog.models import Counterparty, Warehouse, Product
from documents.models import Document
from documents.services import uuid
from inventory.models import StockDraft
from payments.models import MoneyAccount, Allocation, MoneyMovement
from posting.models import Balance, Movement

ZERO=Decimal('0.00')
MONEY=DecimalField(max_digits=25,decimal_places=2)
CURRENT='Поточний стан проведених документів за їх датою. Чернетки та сторновані документи виключені; це не відновлення історичного стану на минулу дату.'
LEDGER='Рухи за датою запису, включно зі сторно окремим зворотним рухом. Чернетки не враховуються.'
DEBT='Поточна заборгованість за документами обраного періоду. Оплати враховуються на зараз незалежно від дати платежу. Аванси не погашають борг автоматично. Товарні повернення ще не коригують фінансовий борг.'
MARGIN='Попередній управлінський розрахунок: продажі мінус зафіксована собівартість. Без ПДВ та інших витрат. Фінансові коригування повернень ще не реалізовані; бухгалтерське приймання алгоритму відкладено власником. Не фінальний прибуток.'

# (label, columns, supported filters, explanation). One schema drives UI and CSV.
SCHEMAS={
 'sales':('Продажі',[('date','Дата','date'),('number','Документ','text'),('counterparty','Контрагент','text'),('warehouse','Склад','text'),('total','Сума, грн','money')],{'from','to','counterparty','warehouse'},CURRENT),
 'balances':('Поточні залишки',[('product','Товар','text'),('warehouse','Склад','text'),('quantity','Кількість','quantity'),('unit','Одиниця','text'),('value','Вартість, грн','money'),('average','Середня собівартість, грн','average')],{'warehouse','product'},'Поточні залишки на момент формування, без фільтра за датою. Назви з поточних довідників; кількості різних одиниць не підсумовуються.'),
 'stock-movements':('Рухи товарів',[('date','Дата','date'),('number','Документ','text'),('product','Товар','text'),('warehouse','Склад','text'),('quantity','Рух кількості','quantity'),('unit','Одиниця','text'),('value','Рух вартості, грн','money'),('action','Дія','text')],{'from','to','warehouse','product'},LEDGER),
 'payments':('Рухи грошей',[('date','Дата','date'),('number','Платіж','text'),('counterparty','Контрагент','text'),('account','Каса / рахунок','text'),('amount','Рух грошей, грн','money'),('action','Дія','text')],{'from','to','counterparty','account'},LEDGER+' Лише внутрішній облік, без банківської звірки та ПРРО.'),
 'debts-sales':('Борг покупців',[('date','Дата документа','date'),('number','Продаж','text'),('counterparty','Контрагент','text'),('total','Сума, грн','money'),('paid','Оплачено, грн','money'),('due','Борг, грн','money'),('payment_status','Стан оплати','text')],{'from','to','counterparty'},DEBT),
 'debts-receipts':('Борг постачальникам',[('date','Дата документа','date'),('number','Надходження','text'),('counterparty','Контрагент','text'),('total','Сума, грн','money'),('paid','Оплачено, грн','money'),('due','Борг, грн','money'),('payment_status','Стан оплати','text')],{'from','to','counterparty'},DEBT),
 'margin':('Валовий результат · попередній',[('date','Дата','date'),('number','Продаж','text'),('counterparty','Контрагент','text'),('total','Продажі, грн','money'),('cost','Собівартість, грн','money'),('margin','Валовий результат, грн','money')],{'from','to','counterparty','warehouse'},CURRENT+' '+MARGIN),
}


def decimal(value,places=2):
    return format((value or ZERO).quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP),f'.{places}f')


def metadata(kind):
    if kind not in SCHEMAS:raise ValidationError('Невідомий тип звіту.')
    title,columns,filters,note=SCHEMAS[kind]
    return {'kind':kind,'title':title,'columns':[{'key':k,'label':label,'type':t} for k,label,t in columns],'filters':sorted(filters),'note':note}


def parse_filters(org,kind,params):
    metadata(kind)
    allowed=SCHEMAS[kind][2]
    if set(params)-allowed-{'page'}:raise ValidationError('Цей звіт не підтримує передані фільтри.')
    if any(len(params.getlist(k))!=1 for k in params):raise ValidationError('Не повторюйте параметри фільтра.')
    result={}
    for field in ('from','to'):
        if params.get(field):result[field]=forms.DateField(input_formats=['%Y-%m-%d']).clean(params[field])
    if result.get('from') and result.get('to') and result['from']>result['to']:raise ValidationError('Дата «з» пізніша за дату «до».')
    for name,model in [('counterparty',Counterparty),('warehouse',Warehouse),('product',Product),('account',MoneyAccount)]:
        if params.get(name):
            key=uuid(params[name])
            if not model.objects.filter(organization=org,pk=key).exists():raise ValidationError('Фільтр містить запис іншої організації або недоступний запис.')
            result[name]=key
    try:page=int(params.get('page','1'))
    except ValueError:raise ValidationError('Невірна сторінка.') from None
    if not 1<=page<=10000:raise ValidationError('Невірна сторінка.')
    return result,page


def date_scope(query,filters):
    if 'from' in filters:query=query.filter(date__gte=filters['from'])
    if 'to' in filters:query=query.filter(date__lte=filters['to'])
    return query


def queryset(org,kind,filters):
    if kind in ('sales','margin','debts-sales','debts-receipts'):
        model=StockDraft if kind=='debts-receipts' else Document
        query=model.objects.filter(organization=org,status='posted')
        if kind=='debts-receipts':query=query.filter(kind='receipt',total__isnull=False)
        for field in ('counterparty','warehouse'):
            if field in filters:query=query.filter(**{field+'_id':filters[field]})
        if kind in ('sales','margin'):query=query.select_related('posting').filter(posting__is_reversed=False)
        if kind.startswith('debts-'):
            field='receipt' if kind=='debts-receipts' else 'sale'
            allocated=Allocation.objects.filter(**{field:OuterRef('pk')},payment__organization=org,payment__status='posted').order_by().values(field).annotate(value=Sum('amount')).values('value')
            query=query.annotate(paid=Coalesce(Subquery(allocated,output_field=MONEY),Value(ZERO,output_field=MONEY))).annotate(due=F('total')-F('paid'))
        return date_scope(query,filters).order_by('date','created_at','id')
    if kind=='balances':
        query=Balance.objects.filter(organization=org).select_related('product','warehouse','unit')
        for field in ('warehouse','product'):
            if field in filters:query=query.filter(**{field+'_id':filters[field]})
        return query.order_by('warehouse__name','product__name','id')
    if kind=='stock-movements':
        query=Movement.objects.filter(organization=org).select_related('posting__sale','posting__stock','posting__operation')
        for field in ('warehouse','product'):
            if field in filters:query=query.filter(**{field+'_id':filters[field]})
        return date_scope(query,filters).order_by('date','id')
    query=MoneyMovement.objects.filter(organization=org).select_related('payment')
    if 'counterparty' in filters:query=query.filter(payment__counterparty_id=filters['counterparty'])
    if 'account' in filters:query=query.filter(account_id=filters['account'])
    return date_scope(query,filters).order_by('date','id')


def row_data(kind,obj):
    if kind=='balances':return {'product':obj.product.name,'warehouse':obj.warehouse.name,'quantity':decimal(obj.quantity,3),'unit':obj.unit.symbol,'value':decimal(obj.value),'average':decimal(obj.value/obj.quantity if obj.quantity else ZERO,6)}
    if kind=='stock-movements':
        document=obj.posting.sale or obj.posting.stock or obj.posting.operation
        return {'date':obj.date.isoformat(),'number':document.display_number,'product':obj.product_name,'warehouse':obj.warehouse_name,'quantity':decimal(obj.quantity,3),'unit':obj.unit_symbol,'value':decimal(obj.value),'action':'Сторно' if obj.is_reversal else 'Проведення'}
    if kind=='payments':return {'date':obj.date.isoformat(),'number':obj.payment.display_number,'counterparty':obj.payment.counterparty_snapshot.get('name',''),'account':obj.account_name,'amount':decimal(obj.amount),'action':'Сторно' if obj.is_reversal else 'Проведення'}
    result={'date':obj.date.isoformat(),'number':obj.display_number,'counterparty':obj.counterparty_snapshot.get('name',''),'total':decimal(obj.total)}
    if kind=='sales':result['warehouse']=obj.warehouse_name or '—'
    if kind=='margin':result.update(cost=decimal(obj.posting.cost_total),margin=decimal(obj.total-obj.posting.cost_total))
    if kind.startswith('debts-'):
        due=Decimal(decimal(obj.due));paid=Decimal(decimal(obj.paid))
        result.update(paid=decimal(paid),due=decimal(due),payment_status='Оплачено' if due==0 else ('Частково' if paid else 'Не оплачено'))
    return result


def summary(kind,query):
    fields={'sales':{'total':Sum('total')},'margin':{'total':Sum('total'),'cost':Sum('posting__cost_total')},'balances':{'value':Sum('value')},'stock-movements':{'value':Sum('value')},'payments':{'inflow':Sum('amount',filter=Q(amount__gt=0)),'outflow':Sum('amount',filter=Q(amount__lt=0)),'net':Sum('amount')},'debts-sales':{'total':Sum('total'),'paid':Sum('paid'),'due':Sum('due')},'debts-receipts':{'total':Sum('total'),'paid':Sum('paid'),'due':Sum('due')}}[kind]
    values={key.removeprefix('report_sum_'):Decimal(decimal(value)) for key,value in query.aggregate(**{'report_sum_'+key:value for key,value in fields.items()}).items()}
    if kind=='margin':values['margin']=values['total']-values['cost']
    if kind=='payments':values['outflow']=-values['outflow']
    return {key:decimal(value) for key,value in values.items()}


def report(org,kind,params,export=False):
    filters,page=parse_filters(org,kind,params)
    query=queryset(org,kind,filters)
    with localcontext() as context:
        context.prec=42
        count=query.count()
        if export and count>10000:raise ValidationError('Експорт обмежений 10 000 рядків. Звузьте фільтри; частковий файл не створено.')
        data=metadata(kind)
        data.update(filters_applied={key:str(value) for key,value in filters.items()},total=count,page=page,summary=summary(kind,query),items=[row_data(kind,obj) for obj in (query if export else query[(page-1)*50:page*50])])
        return data
