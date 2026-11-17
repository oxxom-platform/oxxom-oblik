import time
from datetime import datetime, timedelta, timezone
from functools import wraps
import pyotp
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.http import JsonResponse, FileResponse
from pathlib import Path
from django.shortcuts import render, redirect
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST, require_GET
from .access import memberships_for
from .models import User, TOTPDevice, AuthThrottle
from .mfa import encrypt, decrypt, consume_totp, consume_recovery, recovery_codes, provisioning_qr


def has_mfa_session(request, now):
    started = request.session.get("mfa_started", 0)
    return bool(request.user.is_authenticated and request.user.is_active and started
                and 0 <= now - started < settings.SESSION_COOKIE_AGE)


def protected(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        now = time.time()
        started = request.session.get("mfa_started", 0)
        if not has_mfa_session(request, now):
            logout(request)
            return JsonResponse({"error": "authentication_required"}, status=401)
        request.session["mfa_last"] = now
        request.session.set_expiry(datetime.fromtimestamp(started, timezone.utc) + timedelta(seconds=settings.SESSION_COOKIE_AGE))
        return view(request, *args, **kwargs)
    return never_cache(wrapped)


def throttle_rows(request, identity):
    # Trust only REMOTE_ADDR; forwarded headers require a separately reviewed proxy policy.
    keys = ["account:" + identity, "source:" + request.META.get("REMOTE_ADDR", "unknown")]
    records = []
    now = time.time()
    for key in sorted(salted_hmac("auth-throttle", key).hexdigest() for key in keys):
        record, _ = AuthThrottle.objects.get_or_create(key=key)
        record = AuthThrottle.objects.select_for_update().get(pk=record.pk)
        if now - record.window_started >= 900:
            record.window_started = now
            record.failures = 0
        records.append(record)
    return records


def failure(records):
    for record in records:
        record.failures += 1
        record.save(update_fields=["failures", "window_started"])
    return JsonResponse({"error": "invalid_credentials"}, status=400)


def blocked(records):
    return any(record.failures >= 5 for record in records)


def unavailable():
    return JsonResponse({"error": "authentication_unavailable"}, status=503)


@never_cache
@ensure_csrf_cookie
@require_GET
def auth_page(request):
    security_mode = request.GET.get('security') == '1'
    if not security_mode and has_mfa_session(request, time.time()):
        return redirect('/app/')
    return render(request, "accounts/login.html", {'security_mode': security_mode})


@require_GET
def login_script(request):
    path = Path(__file__).parent / 'static' / 'accounts' / 'login.js'
    response = FileResponse(path.open('rb'), content_type='text/javascript')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


@never_cache
@require_POST
@sensitive_post_parameters()
@sensitive_variables()
def password_login(request):
    if not settings.OXXOM_MFA_KEY:
        return unavailable()
    email = request.POST.get("email", "").strip().lower()
    password = request.POST.get("password", "")
    if len(email) > 254 or len(password) > 1024:
        return JsonResponse({"error": "invalid_credentials"}, status=400)
    # A new password attempt always discards any previous challenge or full login.
    logout(request)
    with transaction.atomic():
        records = throttle_rows(request, email)
        if blocked(records):
            return JsonResponse({"error": "too_many_attempts"}, status=429)
        user = authenticate(request, username=email, password=password)
        if user is None:
            return failure(records)
        request.session.cycle_key()
        request.session["pending_user"] = str(user.pk)
        request.session["pending_hash"] = user.get_session_auth_hash()
        request.session["pending_started"] = time.time()
        request.session.set_expiry(300)
        return JsonResponse({"next": "verify" if TOTPDevice.objects.filter(user=user, confirmed=True).exists() else "setup"})


def pending_user(request):
    if time.time() - request.session.get("pending_started", 0) >= 300:
        return None
    user = User.objects.select_for_update().filter(pk=request.session.get("pending_user"), is_active=True).first()
    if user is None or not constant_time_compare(user.get_session_auth_hash(), request.session.get("pending_hash", "")):
        return None
    return user


@never_cache
@require_POST
@sensitive_post_parameters()
@sensitive_variables()
def setup(request):
    if not settings.OXXOM_MFA_KEY:
        return unavailable()
    with transaction.atomic():
        user = pending_user(request)
        if user is None:
            return JsonResponse({"error": "password_required"}, status=401)
        device, _ = TOTPDevice.objects.get_or_create(user=user, defaults={"secret": encrypt(pyotp.random_base32())})
        if device.confirmed:
            return JsonResponse({"error": "already_configured"}, status=409)
        try:
            secret = decrypt(device.secret)
        except InvalidToken:
            return unavailable()
        uri = pyotp.TOTP(secret).provisioning_uri(user.email, issuer_name="OXXOM OBLIK")
        return JsonResponse({"secret": secret, "uri": uri, "qr_code": provisioning_qr(uri)})


@never_cache
@require_POST
@sensitive_post_parameters()
@sensitive_variables()
def verify(request):
    if not settings.OXXOM_MFA_KEY:
        return unavailable()
    code = request.POST.get("code", "").strip()
    if len(code) > 32:
        code = ""
    with transaction.atomic():
        user = pending_user(request)
        if user is None:
            return JsonResponse({"error": "password_required"}, status=401)
        records = throttle_rows(request, user.email)
        if blocked(records):
            return JsonResponse({"error": "too_many_attempts"}, status=429)
        device = TOTPDevice.objects.filter(user=user).first()
        valid = False
        if device:
            try:
                valid = consume_totp(device, code) or (device.confirmed and consume_recovery(user, code))
            except InvalidToken:
                return unavailable()
        if not valid:
            return failure(records)
        codes = None
        if not device.confirmed:
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            codes = recovery_codes(user)
        request.session.flush()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session["mfa_started"] = request.session["mfa_last"] = time.time()
        request.session.set_expiry(datetime.fromtimestamp(request.session["mfa_started"], timezone.utc) + timedelta(seconds=settings.SESSION_COOKIE_AGE))
        return JsonResponse({"status": "authenticated", "recovery_codes": codes})


@require_GET
@protected
def organizations(request):
    return JsonResponse({"organizations": [{"id": str(m.organization_id), "name": m.organization.name, "role": m.role} for m in memberships_for(request.user)]})


@never_cache
@require_POST
def sign_out(request):
    logout(request)
    return JsonResponse({"status": "signed_out"})


@require_POST
@protected
@sensitive_post_parameters()
def regenerate_recovery(request):
    if not settings.OXXOM_MFA_KEY:
        return unavailable()
    # Require a fresh TOTP, not a recovery code, for this sensitive operation.
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        records = throttle_rows(request, user.email)
        if blocked(records):
            return JsonResponse({"error": "too_many_attempts"}, status=429)
        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        try:
            valid = device and consume_totp(device, request.POST.get("code", "").strip())
        except InvalidToken:
            return unavailable()
        if not valid:
            return failure(records)
        return JsonResponse({"recovery_codes": recovery_codes(user)})
