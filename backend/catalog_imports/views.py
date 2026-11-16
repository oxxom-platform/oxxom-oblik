import csv
import io
from django.core.exceptions import PermissionDenied,ValidationError,RequestDataTooBig,TooManyFilesSent
from django.db import transaction
from django.http import JsonResponse,HttpResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET,require_POST
from accounts.auth_views import protected
from accounts.access import memberships_for,ROLE_PERMISSIONS,require_permission
from accounts.models import Organization
from documents.services import Conflict
from documents.views import body
from .models import ImportBatch
from .services import SCHEMAS,schema,make_preview,confirm,refresh,MAX_BYTES,MAX_ROWS


def allowed_kinds(user,organization_id):
    member=memberships_for(user).filter(organization_id=organization_id).first()
    permissions=ROLE_PERMISSIONS.get(member.role,()) if member else ()
    return [kind for kind in SCHEMAS if f'catalog.{kind}.write' in permissions]


def page_number(request):
    try:page=int(request.GET.get('page','1'))
    except ValueError:raise ValidationError('Невірна сторінка.') from None
    if not 1<=page<=10000:raise ValidationError('Невірна сторінка.')
    return page


def serialize(batch,page=None):
    data={'id':str(batch.pk),'kind':batch.kind,'filename':batch.filename,'status':batch.status,'version':batch.version,'created_at':batch.created_at.isoformat(),'expires_at':batch.expires_at.isoformat(),'expired':batch.expires_at<=timezone.now(),'applied_at':batch.applied_at.isoformat() if batch.applied_at else None,'counts':batch.counts}
    if page is not None:data.update(rows=batch.plan['rows'][(page-1)*50:page*50],page=page,total=sum(batch.counts.values()))
    return data


def error_response(exc):
    if isinstance(exc,PermissionDenied):return JsonResponse({'error':'Немає прав на імпорт цього довідника.'},status=403)
    if isinstance(exc,Conflict):return JsonResponse({'error':str(exc)},status=409)
    return JsonResponse({'error':' '.join(exc.messages)},status=400)


@protected
def _page(request):return render(request,'catalog_imports/index.html')


@require_GET
@ensure_csrf_cookie
def page(request):
    response=_page(request)
    return redirect('/login/') if response.status_code==401 else response


@require_GET
@protected
def options(request,organization_id):
    allowed=allowed_kinds(request.user,organization_id)
    return JsonResponse({'kinds':[{**schema(kind),'kind':kind} for kind in allowed],'max_bytes':MAX_BYTES,'max_rows':MAX_ROWS})


@require_GET
@protected
def template(request,organization_id,kind):
    try:
        config=schema(kind);require_permission(request.user,organization_id,f'catalog.{kind}.write')
        output=io.StringIO(newline='');csv.writer(output,delimiter=';',lineterminator='\r\n').writerow(config['fields'])
        response=HttpResponse(('\ufeff'+output.getvalue()).encode('utf-8'),content_type='text/csv; charset=utf-8')
        response['Content-Disposition']=f'attachment; filename="oxxom-{kind}-template.csv"'
        return response
    except (PermissionDenied,ValidationError) as exc:return error_response(exc)


@require_GET
@protected
def listing(request,organization_id):
    try:
        allowed=allowed_kinds(request.user,organization_id)
        if not allowed:raise PermissionDenied
        page=page_number(request)
        query=ImportBatch.objects.filter(organization_id=organization_id,actor=request.user,kind__in=allowed).defer('inputs','plan','result').order_by('-created_at','-id')
        return JsonResponse({'items':[serialize(batch) for batch in query[(page-1)*50:page*50]],'total':query.count(),'page':page})
    except (PermissionDenied,ValidationError) as exc:return error_response(exc)


@require_GET
@protected
def detail(request,organization_id,batch_id):
    try:
        batch=get_object_or_404(ImportBatch,pk=batch_id,organization_id=organization_id,actor=request.user)
        require_permission(request.user,organization_id,f'catalog.{batch.kind}.write')
        return JsonResponse({'batch':serialize(batch,page_number(request))})
    except (PermissionDenied,ValidationError) as exc:return error_response(exc)


@require_POST
@protected
def preview(request,organization_id,kind):
    try:
        with transaction.atomic():
            org=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
            schema(kind);require_permission(request.user,organization_id,f'catalog.{kind}.write')
            if set(request.FILES)!={'file'} or len(request.FILES.getlist('file'))!=1:raise ValidationError('Оберіть один CSV-файл.')
            batch=make_preview(org,request.user,kind,request.FILES['file'])
            return JsonResponse({'batch':serialize(batch,1)},status=201)
    except (RequestDataTooBig,TooManyFilesSent):return JsonResponse({'error':'Оберіть один CSV до 1 МБ.'},status=400)
    except (PermissionDenied,ValidationError) as exc:return error_response(exc)


def mutate(request,organization_id,batch_id,action):
    try:
        with transaction.atomic():
            get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
            batch=get_object_or_404(ImportBatch.objects.select_for_update().select_related('organization'),pk=batch_id,organization_id=organization_id,actor=request.user)
            require_permission(request.user,organization_id,f'catalog.{batch.kind}.write')
            data=body(request)
            if action=='confirm':return JsonResponse(confirm(batch,request.user,data))
            refresh(batch,request.user,data)
            return JsonResponse({'batch':serialize(batch,1)})
    except (PermissionDenied,ValidationError,Conflict) as exc:return error_response(exc)


@require_POST
@protected
def apply(request,organization_id,batch_id):return mutate(request,organization_id,batch_id,'confirm')


@require_POST
@protected
def recheck(request,organization_id,batch_id):return mutate(request,organization_id,batch_id,'refresh')
