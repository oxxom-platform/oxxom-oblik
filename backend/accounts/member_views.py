from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_POST
from .access import require_permission
from .auth_views import protected
from .models import User, Organization, Membership, AccessEvent


def denied():
    return JsonResponse({'error': 'Недостатньо прав доступу.'}, status=403)


def member_data(member):
    return {'id': str(member.pk), 'email': member.user.email, 'name': member.user.display_name,
            'role': member.role, 'is_active': member.is_active}


def page_number(request):
    try:
        return max(1, min(int(request.GET.get('page', 1)), 10000))
    except ValueError:
        return 1


@protected
def _workspace(request):
    return render(request, 'accounts/workspace.html', {'roles': Membership.Role.choices})


@require_GET
@ensure_csrf_cookie
def workspace(request):
    response = _workspace(request)
    return redirect('/login/') if response.status_code == 401 else response


@require_GET
@protected
def members(request, organization_id):
    try:
        require_permission(request.user, organization_id, 'members.manage')
    except PermissionDenied:
        return denied()
    page = page_number(request)
    records = Membership.objects.filter(organization_id=organization_id).select_related('user').order_by('created_at', 'id')
    return JsonResponse({'members': [member_data(m) for m in records[(page-1)*50:page*50]], 'page': page, 'total': records.count()})


@require_POST
@protected
@sensitive_post_parameters()
@sensitive_variables()
def add_member(request, organization_id):
    email = request.POST.get('email', '').strip().lower()
    name = request.POST.get('name', '').strip()
    role = request.POST.get('role', '')
    password = request.POST.get('password', '')
    if role not in Membership.Role.values or len(email) > 254 or len(name) > 120 or len(password) > 1024:
        return JsonResponse({'error': 'Перевірте поля форми.'}, status=400)
    try:
        with transaction.atomic():
            organization = get_object_or_404(Organization.objects.select_for_update(), pk=organization_id)
            require_permission(request.user, organization_id, 'members.manage')
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User(email=email, display_name=name)
                user.set_password(password)
                user.full_clean()
                try:
                    validate_password(password, user)
                except ValidationError as exc:
                    return JsonResponse({'error': 'Перевірте початковий пароль нового акаунта.',
                                         'fields': {'password': exc.messages}}, status=400)
                user.save()
            if not user.is_active:
                return JsonResponse({'error': 'Акаунт заблоковано. Зверніться до адміністратора.'}, status=409)
            if Membership.objects.filter(user=user, organization=organization).exists():
                return JsonResponse({'error': 'Працівник уже є в організації. Змініть його доступ у списку.'}, status=409)
            member = Membership.objects.create(user=user, organization=organization, role=role)
            AccessEvent.objects.create(organization=organization, actor=request.user, target=member, action='member.add', details={'role': role, 'is_active': True})
            return JsonResponse({'member': member_data(member)}, status=201)
    except PermissionDenied:
        return denied()
    except ValidationError as exc:
        # Django validation messages contain no submitted passwords.
        return JsonResponse({'error': ' '.join(exc.messages), 'fields': getattr(exc, 'message_dict', {})}, status=400)
    except IntegrityError:
        return JsonResponse({'error': 'Дані змінилися. Оновіть список і повторіть.'}, status=409)


@require_POST
@protected
def change_member(request, organization_id, membership_id):
    role = request.POST.get('role', '')
    active = request.POST.get('is_active', '')
    if role not in Membership.Role.values or active not in ('true', 'false'):
        return JsonResponse({'error': 'Невірна роль або стан доступу.'}, status=400)
    try:
        with transaction.atomic():
            # All membership mutations lock the organization first. This also protects
            # against two owners removing each other concurrently.
            organization = get_object_or_404(Organization.objects.select_for_update(), pk=organization_id)
            require_permission(request.user, organization_id, 'members.manage')
            member = get_object_or_404(Membership.objects.select_related('user'), pk=membership_id, organization=organization)
            enabled = active == 'true'
            if member.role == Membership.Role.OWNER and member.is_active and (role != Membership.Role.OWNER or not enabled):
                owners = Membership.objects.filter(organization=organization, role=Membership.Role.OWNER, is_active=True, user__is_active=True)
                if not owners.exclude(pk=member.pk).exists():
                    return JsonResponse({'error': 'В організації має залишитися активний власник.'}, status=409)
            before = {'role': member.role, 'is_active': member.is_active}
            member.role, member.is_active = role, enabled
            member.save(update_fields=['role', 'is_active'])
            AccessEvent.objects.create(organization=organization, actor=request.user, target=member, action='member.change', details={'before': before, 'after': {'role': role, 'is_active': enabled}})
            return JsonResponse({'member': member_data(member)})
    except PermissionDenied:
        return denied()


@require_GET
@protected
def access_events(request, organization_id):
    try:
        require_permission(request.user, organization_id, 'members.manage')
    except PermissionDenied:
        return denied()
    page = page_number(request)
    events = AccessEvent.objects.filter(organization_id=organization_id).select_related('actor', 'target__user').order_by('-created_at', '-id')
    return JsonResponse({'events': [{'action': e.action, 'actor': e.actor.email, 'target': e.target.user.email,
        'created_at': e.created_at.isoformat(), 'details': e.details} for e in events[(page-1)*50:page*50]], 'page': page, 'total': events.count()})
