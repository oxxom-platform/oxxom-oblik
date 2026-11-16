import csv
import io
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.cache import never_cache
from accounts.access import require_permission, memberships_for, ROLE_PERMISSIONS
from accounts.auth_views import protected
from accounts.models import Organization
from .services import SCHEMAS,metadata,report


@protected
def _page(request):return render(request,'reports/index.html')


@require_GET
@ensure_csrf_cookie
def page(request):
    response=_page(request)
    return redirect('/login/') if response.status_code==401 else response


@require_GET
@protected
def options(request,organization_id):
    member=memberships_for(request.user).filter(organization_id=organization_id).first()
    allowed=bool(member and 'reports.read' in ROLE_PERMISSIONS.get(member.role,()))
    return JsonResponse({'read':allowed,'reports':[metadata(kind) for kind in SCHEMAS] if allowed else []})


def csv_text(value):
    text=str(value)
    # Leading whitespace must not hide spreadsheet formula prefixes.
    if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')):return "'"+text
    return text


def build_csv(data):
    output=io.StringIO(newline='')
    writer=csv.writer(output,delimiter=';',lineterminator='\r\n')
    writer.writerow([column['label'] for column in data['columns']])
    for row in data['items']:
        writer.writerow([csv_text(row[column['key']]) if column['type'] in ('text','date') else row[column['key']] for column in data['columns']])
    return ('\ufeff'+output.getvalue()).encode('utf-8')


def response(request,organization_id,kind,export=False):
    try:
        # Business mutations use this same organization lock. Each report has one
        # consistent snapshot for page rows, totals and allocations under READ COMMITTED.
        with transaction.atomic():
            org=get_object_or_404(Organization.objects.select_for_update(),pk=organization_id)
            require_permission(request.user,organization_id,'reports.read')
            data=report(org,kind,request.GET,export)
            if not export:return JsonResponse(data)
            result=HttpResponse(build_csv(data),content_type='text/csv; charset=utf-8')
            result['Content-Disposition']=f'attachment; filename="oxxom-{kind}.csv"'
            return result
    except PermissionDenied:return JsonResponse({'error':'Звіти з фінансовими сумами доступні власнику та бухгалтеру.'},status=403)
    except ValidationError as exc:return JsonResponse({'error':' '.join(exc.messages)},status=400)


@require_GET
@never_cache
@protected
def data(request,organization_id,kind):return response(request,organization_id,kind)


@require_GET
@never_cache
@protected
def export(request,organization_id,kind):return response(request,organization_id,kind,True)
