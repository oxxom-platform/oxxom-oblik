"""Shared navigation uses the same explicit organization grants as business APIs."""
from uuid import UUID
from .i18n import translate
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET
from accounts.access import memberships_for, ROLE_PERMISSIONS
from accounts.auth_views import protected


SECTIONS = (
    ('home', '/app/', 'Огляд', 'Робочий простір', None),
    ('sales', '/documents/', 'Продажі', 'Облік', 'records.read'),
    ('inventory', '/inventory/', 'Надходження', 'Облік', ('records.read', 'stock.read')),
    ('operations', '/operations/', 'Складські операції', 'Облік', ('records.read', 'stock.read')),
    ('stock', '/stock/', 'Залишки та рухи', 'Облік', ('records.read', 'stock.read')),
    ('payments', '/payments/', 'Оплати та гроші', 'Облік', 'payments.manage'),
    ('reports', '/reports/', 'Звіти', 'Аналітика', 'reports.read'),
    ('catalog', '/catalog/', 'Довідники', 'Налаштування', ('catalog.products.read', 'catalog.units.read')),
    ('imports', '/imports/', 'CSV-імпорт', 'Налаштування', tuple(f'catalog.{k}.write' for k in ('units', 'products', 'warehouses', 'counterparties'))),
    ('issuer', '/organization-details/', 'Реквізити', 'Налаштування', 'records.read'),
    ('team', '/team/', 'Працівники та доступ', 'Налаштування', 'members.manage'),
)
PATHS = {entry[1] for entry in SECTIONS}


def sections(member):
    rights = ROLE_PERMISSIONS.get(member.role, ()) if member else ()
    result = []
    for key, path, label, group, required in SECTIONS:
        if required is not None and not any(p in rights for p in ((required,) if isinstance(required, str) else required)):
            continue
        result.append({'key': key, 'path': path, 'label': translate(label), 'group': translate(group)})
    return result


def context(request):
    if request.path not in PATHS or not request.user.is_authenticated:
        return {}
    members = list(memberships_for(request.user).order_by('organization__name', 'organization_id'))
    requested = request.GET.get('organization')
    if requested is not None:
        try:
            requested = str(UUID(requested))
        except (ValueError, TypeError):
            raise PermissionDenied('Організація недоступна.') from None
        member = next((m for m in members if str(m.organization_id) == requested), None)
        if member is None:
            raise PermissionDenied('Організація недоступна.')
    else:
        saved = request.session.get('workspace_organization')
        member = next((m for m in members if str(m.organization_id) == saved), members[0] if members else None)
    if member:
        selected = str(member.organization_id)
        if request.session.get('workspace_organization') != selected:
            request.session['workspace_organization'] = selected
    else:
        selected = ''
        request.session.pop('workspace_organization', None)
    nav = sections(member)
    for entry in nav:
        entry['active'] = request.path == entry['path']
        entry['url'] = entry['path'] + ('?organization=' + selected if selected else '')
    return {'workspace': {
        'organization': selected, 'name': member.organization.name if member else '',
        'role': translate(member.get_role_display()) if member else '', 'navigation': nav,
        'email': request.user.email,
        'config': {'organization': selected, 'organizations': [str(m.organization_id) for m in members],
                   'paths': sorted(PATHS), 'allowedPaths': [e['path'] for e in nav]},
    }}


@protected
def _page(request):
    return render(request, 'core/dashboard.html')


@require_GET
@ensure_csrf_cookie
def page(request):
    response = _page(request)
    return redirect('/login/') if response.status_code == 401 else response


@require_GET
@protected
def overview(request, organization_id):
    member = memberships_for(request.user).filter(organization_id=organization_id).first()
    if not member:
        return JsonResponse({'error': 'Організація недоступна.'}, status=403)
    from documents.models import Document
    from inventory.models import StockDraft
    from operations.models import Operation
    from payments.models import Payment
    rights = ROLE_PERMISSIONS[member.role]
    cards = []
    entries = [('sales', Document, 'records.read', 'sales.draft', 'Нова реалізація'),
               ('inventory', StockDraft, ('records.read', 'stock.read'), 'stock.draft', 'Нове надходження'),
               ('operations', Operation, ('records.read', 'stock.read'), ('stock.draft', 'sales.draft'), 'Нова операція'),
               ('payments', Payment, 'payments.manage', 'payments.manage', 'Новий платіж')]
    nav = {entry['key']: entry for entry in sections(member)}
    for key, model, read, write, label in entries:
        if not any(p in rights for p in ((read,) if isinstance(read, str) else read)):
            continue
        qs = model.objects.filter(organization_id=organization_id, is_active=True, status='draft')
        if key == 'operations' and 'records.read' not in rights:
            qs = qs.exclude(kind='return')
        writable = any(p in rights for p in ((write,) if isinstance(write, str) else write))
        path = nav[key]['path'] + '?organization=' + str(organization_id)
        cards.append({'key': key, 'label': nav[key]['label'], 'drafts': qs.count(),
                      'url': path, 'create_url': path + '&new=1' if writable else None,
                      'create_label': translate(label)})
    return JsonResponse({'organization': str(organization_id), 'name': member.organization.name,
                         'role': member.get_role_display(), 'cards': cards})
