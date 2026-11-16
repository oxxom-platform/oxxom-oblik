from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from accounts.access import require_permission, memberships_for, ROLE_PERMISSIONS
from accounts.auth_views import protected
from accounts.models import Organization
from documents.views import body
from documents.services import Conflict
from .models import StockDraft, StockEvent
from .services import write_draft


def rights(member):
    permissions = ROLE_PERMISSIONS.get(member.role, frozenset()) if member else frozenset()
    return {'read': bool({'records.read', 'stock.read'} & permissions), 'write': 'stock.draft' in permissions, 'post': 'documents.post' in permissions, 'show_cost': bool(member and member.role in ('owner', 'accountant'))}


def authorize(user, organization_id, write=False):
    member = memberships_for(user).filter(organization_id=organization_id).first()
    granted = rights(member)
    if not granted['write' if write else 'read']:
        raise PermissionDenied
    return granted


def serialize(doc, show_cost, detail=False):
    data = {'id': str(doc.pk), 'number': doc.display_number, 'kind': doc.kind, 'date': doc.date.isoformat(), 'warehouse': str(doc.warehouse_id), 'warehouse_name': doc.warehouse_name, 'note': doc.note, 'version': doc.version, 'is_active': doc.is_active, 'status': doc.status}
    if show_cost:
        data.update(counterparty=str(doc.counterparty_id) if doc.counterparty_id else None, counterparty_snapshot=doc.counterparty_snapshot, total=str(doc.total) if doc.total is not None else None)
    if detail:
        data['lines'] = []
        for row in doc.lines.all():
            line = {'id': str(row.pk), 'product': str(row.product_id), 'name': row.product_name, 'unit_name': row.unit_name, 'unit_symbol': row.unit_symbol, 'quantity': str(row.quantity)}
            if show_cost:
                line.update(price=str(row.price) if row.price is not None else '', amount=str(row.amount) if row.amount is not None else None)
            data['lines'].append(line)
    return data


@protected
def _page(request):
    return render(request, 'inventory/index.html')


@require_GET
@ensure_csrf_cookie
def page(request):
    response = _page(request)
    return redirect('/login/') if response.status_code == 401 else response


@require_GET
@protected
def permissions(request, organization_id):
    member = memberships_for(request.user).filter(organization_id=organization_id).first()
    return JsonResponse(rights(member))


@require_GET
@protected
def listing(request, organization_id):
    try:
        granted = authorize(request.user, organization_id)
    except PermissionDenied:
        return JsonResponse({'error': 'Доступ заборонено.'}, status=403)
    status = request.GET.get('status', 'active')
    if status not in ('active', 'archived', 'all'):
        return JsonResponse({'error': 'Невірний стан.'}, status=400)
    try:
        page = max(1, min(int(request.GET.get('page', '1')), 10000))
    except ValueError:
        return JsonResponse({'error': 'Невірна сторінка.'}, status=400)
    query = StockDraft.objects.filter(organization_id=organization_id)
    if status != 'all':
        query = query.filter(is_active=status == 'active')
    search = request.GET.get('q', '').strip()[:100]
    if search:
        query = query.filter(note__icontains=search)
    query = query.order_by('-date', '-created_at', '-id')
    return JsonResponse({'items': [serialize(doc, granted['show_cost']) for doc in query[(page-1)*50:page*50]], 'total': query.count(), 'page': page})


@require_GET
@protected
def detail(request, organization_id, document_id):
    try:
        granted = authorize(request.user, organization_id)
    except PermissionDenied:
        return JsonResponse({'error': 'Доступ заборонено.'}, status=403)
    return JsonResponse({'document': serialize(get_object_or_404(StockDraft, pk=document_id, organization_id=organization_id), granted['show_cost'], True)})


def mutate(request, organization_id, document_id=None, operation='save'):
    try:
        with transaction.atomic():
            organization = get_object_or_404(Organization.objects.select_for_update(), pk=organization_id)
            granted = authorize(request.user, organization_id, True)
            document = get_object_or_404(StockDraft.objects.select_for_update(), pk=document_id, organization=organization) if document_id else None
            data = body(request)
            if operation == 'state':
                if type(data.get('version')) is not int or data['version'] != document.version:
                    raise Conflict('Чернетку змінено. Оновіть список.')
                if document.status != 'draft' or type(data.get('is_active')) is not bool:
                    raise ValidationError('Невірна дія для чернетки.')
                document.is_active = data['is_active']
                document.version += 1
                document.updated_at = timezone.now()
                document.save(update_fields=['is_active', 'version', 'updated_at'])
                StockEvent.objects.create(organization=organization, actor=request.user, document=document, action='state', version=document.version)
            else:
                document = write_draft(organization, request.user, data, document, granted['show_cost'])
            return JsonResponse({'document': serialize(document, granted['show_cost'], True)}, status=200 if document_id else 201)
    except PermissionDenied:
        return JsonResponse({'error': 'Доступ заборонено.'}, status=403)
    except ValidationError as exc:
        return JsonResponse({'error': ' '.join(exc.messages)}, status=400)
    except Conflict as exc:
        return JsonResponse({'error': str(exc)}, status=409)


@require_POST
@protected
def add(request, organization_id):
    return mutate(request, organization_id)


@require_POST
@protected
def update(request, organization_id, document_id):
    return mutate(request, organization_id, document_id)


@require_POST
@protected
def state(request, organization_id, document_id):
    return mutate(request, organization_id, document_id, 'state')
