import json
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from accounts.access import require_permission, memberships_for, ROLE_PERMISSIONS
from accounts.auth_views import protected
from accounts.models import Organization
from .models import Document, DocumentEvent
from .services import write_draft, Conflict


def serialize(document, detail=False):
    result = {'id': str(document.pk), 'number': document.display_number, 'date': document.date.isoformat(),
        'counterparty': str(document.counterparty_id), 'counterparty_snapshot': document.counterparty_snapshot,
        'warehouse': str(document.warehouse_id) if document.warehouse_id else None, 'warehouse_name': document.warehouse_name,
        'status': document.status, 'is_active': document.is_active, 'version': document.version, 'total': str(document.total), 'note': document.note}
    if detail:
        result['lines'] = [{'id': str(line.pk), 'product': str(line.product_id), 'name': line.product_name, 'kind': line.kind,
            'unit_name': line.unit_name, 'unit_symbol': line.unit_symbol, 'quantity': str(line.quantity), 'price': str(line.price), 'amount': str(line.amount)} for line in document.lines.all()]
    return result


def body(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValidationError('Невірний JSON документа.') from None
    if not isinstance(data, dict):
        raise ValidationError('Очікується об’єкт документа.')
    return data


@protected
def _page(request):
    return render(request, 'documents/index.html')


@require_GET
@ensure_csrf_cookie
def page(request):
    response = _page(request)
    return redirect('/login/') if response.status_code == 401 else response


@require_GET
@protected
def permissions(request, organization_id):
    member = memberships_for(request.user).filter(organization_id=organization_id).first()
    rights = ROLE_PERMISSIONS.get(member.role, frozenset()) if member else frozenset()
    return JsonResponse({'read': 'records.read' in rights, 'write': 'sales.draft' in rights, 'post': 'documents.post' in rights})


@require_GET
@protected
def listing(request, organization_id):
    try:
        require_permission(request.user, organization_id, 'records.read')
    except PermissionDenied:
        return JsonResponse({'error': 'Доступ заборонено.'}, status=403)
    status = request.GET.get('status', 'active')
    if status not in ('active', 'archived', 'all'):
        return JsonResponse({'error': 'Невірний стан.'}, status=400)
    try:
        page = max(1, min(int(request.GET.get('page', '1')), 10000))
    except ValueError:
        return JsonResponse({'error': 'Невірна сторінка.'}, status=400)
    query = Document.objects.filter(organization_id=organization_id)
    if status != 'all':
        query = query.filter(is_active=status == 'active')
    search = request.GET.get('q', '').strip()[:100]
    if search:
        query = query.filter(Q(counterparty_snapshot__name__icontains=search) | Q(note__icontains=search))
    query = query.order_by('-date', '-created_at', '-id')
    return JsonResponse({'items': [serialize(doc) for doc in query[(page-1)*50:page*50]], 'total': query.count(), 'page': page})


@require_GET
@protected
def detail(request, organization_id, document_id):
    try:
        require_permission(request.user, organization_id, 'records.read')
    except PermissionDenied:
        return JsonResponse({'error': 'Доступ заборонено.'}, status=403)
    return JsonResponse({'document': serialize(get_object_or_404(Document, pk=document_id, organization_id=organization_id), True)})


def mutate(request, organization_id, document_id=None, operation='save'):
    try:
        with transaction.atomic():
            organization = get_object_or_404(Organization.objects.select_for_update(), pk=organization_id)
            require_permission(request.user, organization_id, 'sales.draft')
            document = get_object_or_404(Document.objects.select_for_update(), pk=document_id, organization=organization) if document_id else None
            data = body(request)
            if operation == 'state':
                if type(data.get('version')) is not int or data['version'] != document.version:
                    raise Conflict('Документ змінено. Оновіть список.')
                if document.status != 'draft':
                    raise Conflict('Архівування доступне лише для чернетки.')
                if type(data.get('is_active')) is not bool:
                    raise ValidationError('Невірний стан.')
                document.is_active = data['is_active']
                document.version += 1
                document.updated_at = timezone.now()
                document.save(update_fields=['is_active', 'version', 'updated_at'])
                DocumentEvent.objects.create(organization=organization, actor=request.user, document=document, action='state', version=document.version)
            elif operation == 'copy':
                if type(data.get('version')) is not int or data['version'] != document.version:
                    raise Conflict('Документ змінено. Оновіть список.')
                # A new copy uses current active catalog snapshots, retaining source prices.
                copied = {'date': timezone.localdate().isoformat(), 'note': document.note,
                    'counterparty': str(document.counterparty_id), 'warehouse': str(document.warehouse_id) if document.warehouse_id else None,
                    'lines': [{'product': str(line.product_id), 'quantity': str(line.quantity), 'price': str(line.price)} for line in document.lines.all()]}
                document = write_draft(organization, request.user, copied, action='copy')
            else:
                document = write_draft(organization, request.user, data, document)
            return JsonResponse({'document': serialize(document, True)}, status=201 if document_id is None or operation == 'copy' else 200)
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


@require_POST
@protected
def copy(request, organization_id, document_id):
    return mutate(request, organization_id, document_id, 'copy')
