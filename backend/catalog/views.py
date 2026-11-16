from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from accounts.access import require_permission, ROLE_PERMISSIONS, memberships_for
from accounts.auth_views import protected
from accounts.models import Organization
from .forms import FORMS, MODELS
from .models import CatalogEvent


def check_kind(kind):
    if kind not in MODELS:
        raise Http404


def record_data(record, kind, role):
    data = {'id': str(record.pk), 'name': record.name, 'is_active': record.is_active, 'version': record.version}
    for field in FORMS[kind]._meta.fields:
        if field == 'price' and role == 'storekeeper':
            continue
        value = getattr(record, field + '_id') if field == 'unit' else getattr(record, field)
        data[field] = str(value) if field in ('unit', 'price') else value
    if kind == 'products':
        data['unit_name'] = record.unit.symbol
    return data


@protected
def _page(request):
    return render(request, 'catalog/index.html')


@require_GET
@ensure_csrf_cookie
def page(request):
    response = _page(request)
    return redirect('/login/') if response.status_code == 401 else response


@require_GET
@protected
def permissions(request, organization_id):
    membership = memberships_for(request.user).filter(organization_id=organization_id).first()
    if membership is None:
        return JsonResponse({'error': 'Доступ заборонено.'}, status=403)
    rights = ROLE_PERMISSIONS.get(membership.role, frozenset())
    return JsonResponse({'read': [k for k in MODELS if f'catalog.{k}.read' in rights], 'write': [k for k in MODELS if f'catalog.{k}.write' in rights], 'show_price': membership.role != 'storekeeper'})


@require_GET
@protected
def records(request, organization_id, kind):
    check_kind(kind)
    try:
        member = require_permission(request.user, organization_id, f'catalog.{kind}.read')
    except PermissionDenied:
        return JsonResponse({'error': 'Доступ заборонено.'}, status=403)
    status = request.GET.get('status', 'active')
    if status not in ('active', 'archived', 'all'):
        return JsonResponse({'error': 'Невірний фільтр.'}, status=400)
    try:
        page = max(1, min(int(request.GET.get('page', '1')), 10000))
    except ValueError:
        return JsonResponse({'error': 'Невірна сторінка.'}, status=400)
    query = MODELS[kind].objects.filter(organization_id=organization_id)
    if status != 'all':
        query = query.filter(is_active=status == 'active')
    product_kind = request.GET.get('product_kind')
    if kind == 'products' and product_kind is not None:
        if product_kind not in ('goods', 'service'):
            return JsonResponse({'error': 'Невірний тип номенклатури.'}, status=400)
        query = query.filter(kind=product_kind)
    search = request.GET.get('q', '').strip()[:100]
    if search:
        query = query.filter(name__icontains=search)
    if kind == 'products':
        query = query.select_related('unit')
    query = query.order_by('name', 'id')
    return JsonResponse({'items': [record_data(r, kind, member.role) for r in query[(page-1)*50:page*50]], 'total': query.count(), 'page': page})


def save_record(request, organization_id, kind, record_id=None, archive=False):
    check_kind(kind)
    try:
        with transaction.atomic():
            organization = get_object_or_404(Organization.objects.select_for_update(), pk=organization_id)
            member = require_permission(request.user, organization_id, f'catalog.{kind}.write')
            record = get_object_or_404(MODELS[kind].objects.select_for_update(), pk=record_id, organization=organization) if record_id else MODELS[kind](organization=organization)
            if record_id:
                try:
                    version = int(request.POST.get('version', ''))
                except ValueError:
                    return JsonResponse({'error': 'Потрібна версія запису.'}, status=400)
                if version != record.version:
                    return JsonResponse({'error': 'Запис уже змінено. Оновіть список перед збереженням.'}, status=409)
            previous_type = (record.kind, record.unit_id) if kind == 'products' and record_id else None
            if archive:
                active = request.POST.get('is_active')
                if active not in ('true', 'false'):
                    return JsonResponse({'error': 'Невірний стан.'}, status=400)
                record.is_active = active == 'true'
            else:
                options = {'organization': organization} if kind == 'products' else {}
                form = FORMS[kind](request.POST, instance=record, **options)
                if not form.is_valid():
                    return JsonResponse({'error': 'Перевірте поля форми.', 'fields': form.errors.get_json_data()}, status=400)
                if previous_type and previous_type != (record.kind, record.unit_id):
                    from documents.models import DocumentLine
                    from inventory.models import StockLine
                    from operations.models import OperationLine
                    if DocumentLine.objects.filter(product=record).exists() or StockLine.objects.filter(product=record).exists() or OperationLine.objects.filter(product=record).exists():
                        return JsonResponse({'error':'Тип або одиницю використаної позиції змінити не можна. Створіть окрему картку.'}, status=409)
                record = form.save(commit=False)
            if record_id:
                record.version += 1
            record.updated_at = timezone.now()
            record.save()
            CatalogEvent.objects.create(organization=organization, actor=request.user, kind=kind, object_id=record.pk, action='state' if archive else ('update' if record_id else 'create'), version=record.version)
            return JsonResponse({'item': record_data(record, kind, member.role)}, status=200 if record_id else 201)
    except PermissionDenied:
        return JsonResponse({'error': 'Доступ заборонено.'}, status=403)


@require_POST
@protected
def add(request, organization_id, kind):
    return save_record(request, organization_id, kind)


@require_POST
@protected
def update(request, organization_id, kind, record_id):
    return save_record(request, organization_id, kind, record_id)


@require_POST
@protected
def state(request, organization_id, kind, record_id):
    return save_record(request, organization_id, kind, record_id, archive=True)
