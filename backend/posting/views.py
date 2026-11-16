import hashlib
import json
from decimal import Decimal, localcontext
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from accounts.models import Organization
from accounts.access import require_permission, memberships_for, ROLE_PERMISSIONS
from accounts.auth_views import protected
from documents.models import Document
from documents.views import body, serialize as sale_data
from documents.services import uuid, Conflict
from inventory.models import StockDraft
from operations.models import Operation
from inventory.views import serialize as stock_data, authorize
from .models import Balance, Movement, RequestRecord, Period, PeriodEvent
from .services import post_document, reverse_document


def execute(request, organization_id, source, document_id, operation):
    try:
        with transaction.atomic():
            organization = get_object_or_404(Organization.objects.select_for_update(), pk=organization_id)
            require_permission(request.user, organization_id, 'documents.post')
            model = {'sale':Document,'stock':StockDraft,'operation':Operation}[source]
            document = get_object_or_404(model.objects.select_for_update(), pk=document_id, organization=organization)
            data = body(request)
            if set(data) != {'version','idempotency_key'} or type(data['version']) is not int:
                raise ValidationError('Потрібні version та idempotency_key.')
            key = uuid(data['idempotency_key'])
            digest = hashlib.sha256(json.dumps({'source':source,'id':str(document.pk),'operation':operation,'actor':str(request.user.pk),'body':data}, sort_keys=True, separators=(',',':')).encode()).hexdigest()
            record = RequestRecord.objects.filter(organization=organization, key=key).first()
            if record:
                if record.digest != digest:
                    raise Conflict('Цей ключ повторного запиту вже використано з іншими даними.')
                return JsonResponse(record.response)
            if data['version'] != document.version:
                raise Conflict('Документ змінено. Оновіть його перед проведенням.')
            posting = post_document(organization, request.user, document, source) if operation == 'post' else reverse_document(organization, request.user, document, source)
            if source == 'operation':
                from operations.views import serialize as operation_data
                serialized=operation_data(document,True,True)
            else:
                serialized=sale_data(document,True) if source=='sale' else stock_data(document,True,True)
            response = {'document':serialized,
                'posting':{'id':str(posting.pk),'cost_total':str(posting.cost_total),'is_reversed':posting.is_reversed}}
            RequestRecord.objects.create(organization=organization, key=key, digest=digest, response=response)
            return JsonResponse(response)
    except PermissionDenied:
        return JsonResponse({'error':'Недостатньо прав для проведення або сторно.'}, status=403)
    except ValidationError as exc:
        return JsonResponse({'error':' '.join(exc.messages)}, status=400)
    except Conflict as exc:
        return JsonResponse({'error':str(exc)}, status=409)


@require_POST
@protected
def post_sale(request, organization_id, document_id):
    return execute(request, organization_id, 'sale', document_id, 'post')


@require_POST
@protected
def reverse_sale(request, organization_id, document_id):
    return execute(request, organization_id, 'sale', document_id, 'reverse')


@require_POST
@protected
def post_stock(request, organization_id, document_id):
    return execute(request, organization_id, 'stock', document_id, 'post')


@require_POST
@protected
def reverse_stock(request, organization_id, document_id):
    return execute(request, organization_id, 'stock', document_id, 'reverse')


@protected
def _page(request):
    return render(request, 'posting/index.html')


@require_GET
@ensure_csrf_cookie
def page(request):
    response = _page(request)
    return redirect('/login/') if response.status_code==401 else response


def read_options(request, organization_id):
    granted = authorize(request.user, organization_id)
    page = forms.IntegerField(min_value=1,max_value=10000).clean(request.GET.get('page',1))
    return granted, page


@require_GET
@protected
def balances(request, organization_id):
    try:
        granted, page = read_options(request, organization_id)
        query = Balance.objects.filter(organization_id=organization_id).select_related('product','warehouse','unit')
        if request.GET.get('warehouse'):
            query = query.filter(warehouse_id=uuid(request.GET['warehouse']))
        search = request.GET.get('q','').strip()[:100]
        if search:
            query = query.filter(product__name__icontains=search)
        if request.GET.get('include_zero') != 'true':
            query = query.exclude(quantity=0)
        query = query.order_by('warehouse__name','product__name','id')
        items = []
        for balance in query[(page-1)*50:page*50]:
            item = {'product':str(balance.product_id),'name':balance.product.name,'warehouse':str(balance.warehouse_id),'warehouse_name':balance.warehouse.name,'quantity':str(balance.quantity),'unit':balance.unit.symbol}
            if granted['show_cost']:
                with localcontext() as context:
                    context.prec=50
                    average = (balance.value/balance.quantity).quantize(Decimal('0.000001')) if balance.quantity else None
                item.update(value=str(balance.value),average_cost=str(average) if average is not None else None)
            items.append(item)
        return JsonResponse({'items':items,'total':query.count(),'page':page,'show_cost':granted['show_cost']})
    except PermissionDenied:
        return JsonResponse({'error':'Доступ заборонено.'},status=403)
    except ValidationError as exc:
        return JsonResponse({'error':' '.join(exc.messages)},status=400)


@require_GET
@protected
def movements(request, organization_id):
    try:
        granted, page = read_options(request, organization_id)
        query = Movement.objects.filter(organization_id=organization_id).select_related('posting__sale','posting__stock','posting__operation')
        if request.GET.get('warehouse'):
            query = query.filter(warehouse_id=uuid(request.GET['warehouse']))
        search = request.GET.get('q','').strip()[:100]
        if search:
            query = query.filter(product_name__icontains=search)
        query = query.order_by('-id')
        items=[]
        for movement in query[(page-1)*50:page*50]:
            source=movement.posting.sale or movement.posting.stock or movement.posting.operation
            item={'date':movement.date.isoformat(),'name':movement.product_name,'warehouse_name':movement.warehouse_name,'unit':movement.unit_symbol,'quantity':str(movement.quantity),'document_number':source.display_number,'is_reversal':movement.is_reversal}
            if granted['show_cost']:
                item['value']=str(movement.value)
            items.append(item)
        return JsonResponse({'items':items,'total':query.count(),'page':page,'show_cost':granted['show_cost']})
    except PermissionDenied:
        return JsonResponse({'error':'Доступ заборонено.'},status=403)
    except ValidationError as exc:
        return JsonResponse({'error':' '.join(exc.messages)},status=400)


@require_GET
@protected
def period(request, organization_id):
    try:
        require_permission(request.user,organization_id,'documents.post')
    except PermissionDenied:
        return JsonResponse({'error':'Доступ заборонено.'},status=403)
    record=Period.objects.filter(organization_id=organization_id).first()
    member=memberships_for(request.user).get(organization_id=organization_id)
    return JsonResponse({'closed_through':record.closed_through.isoformat() if record and record.closed_through else None,'version':record.version if record else 0,'can_reopen':member.role=='owner'})


@require_POST
@protected
def change_period(request,organization_id):
    try:
        with transaction.atomic():
            organization=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
            require_permission(request.user,organization_id,'periods.close')
            data=body(request)
            record=Period.objects.filter(organization=organization).first()
            version=record.version if record else 0
            if type(data.get('version')) is not int or data['version']!=version:
                raise Conflict('Стан періоду змінено. Оновіть сторінку.')
            if 'closed_through' not in data:
                raise ValidationError('Вкажіть closed_through або null для відкриття всіх дат.')
            if data['closed_through'] is not None and not isinstance(data['closed_through'], str):
                raise ValidationError('Дата має бути рядком YYYY-MM-DD або null.')
            date=forms.DateField(input_formats=['%Y-%m-%d']).clean(data['closed_through']) if data['closed_through'] is not None else None
            if date and date>timezone.localdate():
                raise ValidationError('Не можна закрити майбутній період.')
            old=record.closed_through if record else None
            if old and (date is None or date<old):
                require_permission(request.user,organization_id,'organization.manage')
            reason=forms.CharField(max_length=500).clean(data.get('reason'))
            if record is None:
                record=Period(organization=organization)
            record.closed_through=date
            record.version=version+1
            record.save()
            PeriodEvent.objects.create(organization=organization,actor=request.user,old_date=old,new_date=date,reason=reason)
            return JsonResponse({'closed_through':date.isoformat() if date else None,'version':record.version})
    except PermissionDenied:
        return JsonResponse({'error':'Закрити період може бухгалтер/власник, повторно відкрити — власник.'},status=403)
    except ValidationError as exc:
        return JsonResponse({'error':' '.join(exc.messages)},status=400)
    except Conflict as exc:
        return JsonResponse({'error':str(exc)},status=409)


@require_POST
@protected
def post_operation(request,organization_id,document_id):
    return execute(request,organization_id,'operation',document_id,'post')


@require_POST
@protected
def reverse_operation(request,organization_id,document_id):
    return execute(request,organization_id,'operation',document_id,'reverse')


def save_and_post(request,organization_id,source,document_id=None):
    """One user action: validate/save/post, or leave both DB and stock unchanged."""
    try:
        with transaction.atomic():
            organization=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
            require_permission(request.user,organization_id,'documents.post')
            data=body(request)
            if set(data)!={'document','idempotency_key'} or not isinstance(data['document'],dict):
                raise ValidationError('Потрібні document та idempotency_key.')
            key=uuid(data['idempotency_key'])
            digest=hashlib.sha256(json.dumps({'action':'save_and_post','source':source,'id':str(document_id) if document_id else None,'actor':str(request.user.pk),'body':data},sort_keys=True,separators=(',',':')).encode()).hexdigest()
            previous=RequestRecord.objects.filter(organization=organization,key=key).first()
            if previous:
                if previous.digest!=digest:raise Conflict('Ключ запиту вже використано з іншими даними.')
                return JsonResponse(previous.response)
            model={'sale':Document,'stock':StockDraft,'operation':Operation}[source]
            document=get_object_or_404(model.objects.select_for_update(),pk=document_id,organization=organization) if document_id else None
            payload=data['document']
            if source=='sale':
                require_permission(request.user,organization_id,'sales.draft')
                from documents.services import write_draft
                document=write_draft(organization,request.user,payload,document)
            elif source=='stock':
                require_permission(request.user,organization_id,'stock.draft')
                from inventory.services import write_draft
                document=write_draft(organization,request.user,payload,document,True)
            else:
                from operations.views import grants
                rights=grants(request.user,organization_id)
                if payload.get('kind') not in rights['write'] or (document and document.kind not in rights['write']):raise PermissionDenied
                from operations.services import write_draft
                document=write_draft(organization,request.user,payload,document)
            posting=post_document(organization,request.user,document,source)
            if source=='operation':
                from operations.views import serialize as operation_data
                serialized=operation_data(document,True,True)
            else:serialized=sale_data(document,True) if source=='sale' else stock_data(document,True,True)
            response={'document':serialized,'posting':{'id':str(posting.pk),'cost_total':str(posting.cost_total),'is_reversed':False}}
            RequestRecord.objects.create(organization=organization,key=key,digest=digest,response=response)
            return JsonResponse(response)
    except PermissionDenied:return JsonResponse({'error':'Недостатньо прав для збереження та проведення.'},status=403)
    except ValidationError as exc:return JsonResponse({'error':' '.join(exc.messages)},status=400)
    except Conflict as exc:return JsonResponse({'error':str(exc)},status=409)


@require_POST
@protected
def save_post_sale(request,organization_id,document_id=None):return save_and_post(request,organization_id,'sale',document_id)


@require_POST
@protected
def save_post_stock(request,organization_id,document_id=None):return save_and_post(request,organization_id,'stock',document_id)


@require_POST
@protected
def save_post_operation(request,organization_id,document_id=None):return save_and_post(request,organization_id,'operation',document_id)
