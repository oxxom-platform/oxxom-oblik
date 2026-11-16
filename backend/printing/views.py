from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.views.decorators.http import require_GET,require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from accounts.auth_views import protected
from accounts.access import require_permission,ROLE_PERMISSIONS
from accounts.models import Organization
from documents.models import Document
from documents.views import body
from documents.services import Conflict
from .profiles import get_profile,save_profile
from .models import PrintEvent,ProfileEvent
from .services import prepare


@protected
def _page(request):return render(request,'printing/settings.html')


@require_GET
@ensure_csrf_cookie
def page(request):
    result=_page(request)
    return redirect('/login/') if result.status_code==401 else result


@require_GET
@protected
def profile(request,organization_id):
    try:
        member=require_permission(request.user,organization_id,'records.read')
        data=get_profile(member.organization)
        return JsonResponse({**data,'write':'organization.details' in ROLE_PERMISSIONS[member.role]})
    except PermissionDenied:return JsonResponse({'error':'Немає доступу до реквізитів.'},status=403)


@require_POST
@protected
def update_profile(request,organization_id):
    try:
        with transaction.atomic():
            org=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
            require_permission(request.user,organization_id,'organization.details')
            return JsonResponse(save_profile(org,request.user,body(request)))
    except PermissionDenied:return JsonResponse({'error':'Реквізити змінює власник або бухгалтер.'},status=403)
    except ValidationError as exc:return JsonResponse({'error':' '.join(exc.messages)},status=400)
    except Conflict as exc:return JsonResponse({'error':str(exc)},status=409)


@require_GET
@protected
def profile_events(request,organization_id):
    try:require_permission(request.user,organization_id,'organization.details')
    except PermissionDenied:return JsonResponse({'error':'Недостатньо прав.'},status=403)
    try:page=int(request.GET.get('page','1'))
    except ValueError:return JsonResponse({'error':'Невірна сторінка.'},status=400)
    if not 1<=page<=10000:return JsonResponse({'error':'Невірна сторінка.'},status=400)
    query=ProfileEvent.objects.filter(organization_id=organization_id).select_related('actor').order_by('-id')
    return JsonResponse({'items':[{'version':e.version,'actor':e.actor.email,'created_at':e.created_at.isoformat(),'before':e.before,'after':e.after} for e in query[(page-1)*50:page*50]],'total':query.count(),'page':page})


@protected
def _preview(request,organization_id,document_id,kind):
    try:
        with transaction.atomic():
            org=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
            require_permission(request.user,organization_id,'records.read')
            document=get_object_or_404(Document.objects.select_related('organization'),pk=document_id,organization=org)
            if request.GET:
                if set(request.GET)!={'version'} or len(request.GET.getlist('version'))!=1:raise ValidationError('Невірні параметри друку.')
                try:version=int(request.GET['version'])
                except ValueError:raise ValidationError('Невірна версія документа.') from None
                if version!=document.version:raise Conflict('Документ змінено. Оновіть картку перед друком.')
            context=prepare(document,kind)
            response=render(request,'printing/document.html',context)
            PrintEvent.objects.create(organization=org,document=document,actor=request.user,kind=kind,document_version=document.version,issuer_source=context['issuer_source'])
            response['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'none'"
            return response
    except PermissionDenied:return JsonResponse({'error':'Немає доступу до друку документів цієї організації.'},status=403)
    except (ValidationError,Conflict) as exc:
        message=' '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc)
        return render(request,'printing/error.html',{'message':message,'organization_id':str(organization_id)},status=400 if isinstance(exc,ValidationError) else 409)


@require_GET
def preview(request,organization_id,document_id,kind):
    response=_preview(request,organization_id,document_id,kind)
    return redirect('/login/') if response.status_code==401 else response
