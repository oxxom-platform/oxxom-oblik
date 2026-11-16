from decimal import Decimal,ROUND_HALF_UP,localcontext
from django.core.exceptions import ValidationError
from documents.services import Conflict
from .profiles import snapshot

TITLES={'invoice':'Рахунок на оплату','delivery':'Видаткова накладна','act':'Акт наданих послуг'}


def money(value):return f'{value:,.2f}'.replace(',','\u00a0').replace('.',',')


def quantity(value):return format(value,'.3f').replace('.',',')


def prepare(document,kind):
    if kind not in TITLES:raise ValidationError('Невідома друкована форма.')
    if kind!='invoice' and document.status!='posted':raise Conflict('Накладна та акт доступні лише для проведеної реалізації. Для чернетки використовуйте рахунок.')
    lines=list(document.lines.all())
    if not 1<=len(lines)<=100:raise ValidationError('Документ не містить допустимого набору рядків.')
    with localcontext() as context:
        context.prec=42
        total=sum((row.amount for row in lines),Decimal('0.00'))
        if total!=document.total or any(row.amount!=(row.quantity*row.price).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP) for row in lines):raise Conflict('Суми збереженого документа неузгоджені. Перевірте документ перед друком.')
        wanted={'delivery':'goods','act':'service'}.get(kind)
        selected=[row for row in lines if not wanted or row.kind==wanted]
        if not selected:raise ValidationError('У документі немає товарів для накладної.' if kind=='delivery' else 'У документі немає послуг для акта.')
        subtotal=sum((row.amount for row in selected),Decimal('0.00'))
    issuer=document.issuer_snapshot or snapshot(document.organization)
    source='snapshot' if document.issuer_snapshot else 'current'
    if not issuer:issuer={'legal_name':document.organization.name}
    warnings=[]
    if source=='current':warnings.append('Історичного знімка реквізитів організації немає. Показано поточні реквізити; вони можуть відрізнятися від реквізитів на дату документа.')
    missing=[label for field,label in [('legal_name','назва'),('code','код'),('address','адреса')] if not issuer.get(field)]
    if kind=='invoice' and not issuer.get('bank_details'):missing.append('банківські реквізити')
    if missing:warnings.append('Реквізити організації неповні: '+', '.join(missing)+'.')
    client=document.counterparty_snapshot
    if not client.get('name'):warnings.append('Не заповнена назва контрагента в знімку документа.')
    state={'draft':'ЧЕРНЕТКА · не проведено','reversed':'СТОРНОВАНИЙ ДОКУМЕНТ','posted':'Проведено'}.get(document.status,'Невідомий стан')
    if not document.is_active:state+=' · архів'
    return {'title':TITLES[kind],'kind':kind,'document':document,'issuer':issuer,'issuer_source':source,'client':client,'rows':[{'position':index,'name':row.product_name,'unit':row.unit_symbol,'quantity':quantity(row.quantity),'price':money(row.price),'amount':money(row.amount)} for index,row in enumerate(selected,1)],'total':money(subtotal),'row_count':len(selected),'mixed':len(selected)!=len(lines),'state':state,'warnings':warnings,'date_label':document.date.strftime('%d.%m.%Y')}
