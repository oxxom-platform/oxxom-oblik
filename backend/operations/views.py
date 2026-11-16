from decimal import Decimal
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404,render,redirect
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET,require_POST
from accounts.models import Organization
from accounts.access import memberships_for,ROLE_PERMISSIONS
from accounts.auth_views import protected
from documents.models import Document
from documents.views import body
from documents.services import Conflict,uuid
from .models import Operation,OperationLine,OperationEvent
from .services import write_draft


def grants(user,organization_id):
    member=memberships_for(user).filter(organization_id=organization_id).first()
    rights=ROLE_PERMISSIONS.get(member.role,frozenset()) if member else frozenset()
    read=['transfer','writeoff'] if {'records.read','stock.read'}&rights else []
    if 'records.read' in rights:read.append('return')
    write=['transfer','writeoff'] if 'stock.draft' in rights else []
    if 'sales.draft' in rights:write.append('return')
    return {'read':read,'write':write,'post':'documents.post' in rights,'show_cost':bool(member and member.role in ('owner','accountant'))}


def serialize(doc,show_cost,detail=False):
    data={'id':str(doc.pk),'kind':doc.kind,'number':doc.display_number,'date':doc.date.isoformat(),'warehouse':str(doc.warehouse_id),'warehouse_name':doc.warehouse_name,'target_warehouse':str(doc.target_warehouse_id) if doc.target_warehouse_id else None,'target_name':doc.target_name,'source_sale':str(doc.source_sale_id) if doc.source_sale_id else None,'source_number':doc.source_number,'note':doc.note,'version':doc.version,'status':doc.status,'is_active':doc.is_active}
    if show_cost:data['total']=str(doc.total) if doc.total is not None else None
    if detail:
        data['lines']=[]
        for row in doc.lines.all():
            item={'id':str(row.pk),'product':str(row.product_id),'source_line':str(row.source_line_id) if row.source_line_id else None,'name':row.product_name,'unit_symbol':row.unit_symbol,'quantity':str(row.quantity)}
            if show_cost:item['cost']=str(row.cost) if row.cost is not None else None
            data['lines'].append(item)
    return data


@protected
def _page(request):return render(request,'operations/index.html')


@require_GET
@ensure_csrf_cookie
def page(request):
    response=_page(request)
    return redirect('/login/') if response.status_code==401 else response


@require_GET
@protected
def permissions(request,organization_id):return JsonResponse(grants(request.user,organization_id))


@require_GET
@protected
def listing(request,organization_id):
    rights=grants(request.user,organization_id)
    if not rights['read']:return JsonResponse({'error':'Доступ заборонено.'},status=403)
    try:page=max(1,min(int(request.GET.get('page','1')),10000))
    except ValueError:return JsonResponse({'error':'Невірна сторінка.'},status=400)
    status=request.GET.get('status','active')
    if status not in ('active','archived','all'):return JsonResponse({'error':'Невірний стан.'},status=400)
    query=Operation.objects.filter(organization_id=organization_id,kind__in=rights['read'])
    if status!='all':query=query.filter(is_active=status=='active')
    search=request.GET.get('q','').strip()[:100]
    if search:query=query.filter(note__icontains=search)
    query=query.order_by('-date','-created_at','-id')
    return JsonResponse({'items':[serialize(doc,rights['show_cost']) for doc in query[(page-1)*50:page*50]],'total':query.count(),'page':page})


@require_GET
@protected
def detail(request,organization_id,document_id):
    rights=grants(request.user,organization_id)
    if not rights['read']:return JsonResponse({'error':'Доступ заборонено.'},status=403)
    doc=get_object_or_404(Operation,pk=document_id,organization_id=organization_id)
    if doc.kind not in rights['read']:return JsonResponse({'error':'Доступ заборонено.'},status=403)
    return JsonResponse({'document':serialize(doc,rights['show_cost'],True)})


def mutate(request,organization_id,document_id=None,state=False):
    try:
        with transaction.atomic():
            organization=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
            rights=grants(request.user,organization_id)
            if not rights['write']:raise PermissionDenied
            document=get_object_or_404(Operation.objects.select_for_update(),pk=document_id,organization=organization) if document_id else None
            data=body(request)
            kind=document.kind if state else data.get('kind')
            if kind not in rights['write'] or (document and document.kind not in rights['write']):raise PermissionDenied
            if state:
                if type(data.get('version')) is not int or data['version']!=document.version:raise Conflict('Документ змінено. Оновіть список.')
                if document.status!='draft' or type(data.get('is_active')) is not bool:raise ValidationError('Архівувати можна лише чернетку.')
                document.is_active=data['is_active'];document.version+=1;document.updated_at=timezone.now();document.save(update_fields=['is_active','version','updated_at'])
                OperationEvent.objects.create(organization=organization,actor=request.user,document=document,action='state',version=document.version)
            else:document=write_draft(organization,request.user,data,document)
            return JsonResponse({'document':serialize(document,rights['show_cost'],True)},status=200 if document_id else 201)
    except PermissionDenied:return JsonResponse({'error':'Недостатньо прав для цієї операції.'},status=403)
    except ValidationError as exc:return JsonResponse({'error':' '.join(exc.messages)},status=400)
    except Conflict as exc:return JsonResponse({'error':str(exc)},status=409)


@require_POST
@protected
def add(request,organization_id):return mutate(request,organization_id)


@require_POST
@protected
def update(request,organization_id,document_id):return mutate(request,organization_id,document_id)


@require_POST
@protected
def state(request,organization_id,document_id):return mutate(request,organization_id,document_id,True)


@require_GET
@protected
def return_sources(request,organization_id):
    if 'return' not in grants(request.user,organization_id)['read']:return JsonResponse({'error':'Доступ заборонено.'},status=403)
    try:page=max(1,min(int(request.GET.get('page','1')),10000))
    except ValueError:return JsonResponse({'error':'Невірна сторінка.'},status=400)
    query=Document.objects.filter(organization_id=organization_id,status='posted',warehouse__isnull=False,lines__kind='goods').distinct().order_by('-date','-created_at','-id')
    return JsonResponse({'items':[{'id':str(doc.pk),'number':doc.display_number,'date':doc.date.isoformat(),'warehouse':str(doc.warehouse_id),'warehouse_name':doc.warehouse_name} for doc in query[(page-1)*50:page*50]],'total':query.count()})


@require_GET
@protected
def return_lines(request,organization_id,sale_id):
    if 'return' not in grants(request.user,organization_id)['read']:return JsonResponse({'error':'Доступ заборонено.'},status=403)
    sale=get_object_or_404(Document,pk=sale_id,organization_id=organization_id,status='posted')
    items=[]
    for line in sale.lines.filter(kind='goods'):
        returned=OperationLine.objects.filter(source_line=line,document__kind='return',document__status='posted').aggregate(total=Sum('quantity'))['total'] or 0
        items.append({'id':str(line.pk),'product':str(line.product_id),'name':line.product_name,'unit_symbol':line.unit_symbol,'sold_quantity':str(line.quantity),'remaining':str((line.quantity-returned).quantize(Decimal('0.001')))})
    return JsonResponse({'items':items,'warehouse':str(sale.warehouse_id),'warehouse_name':sale.warehouse_name})
