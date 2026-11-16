"""MFA primitives. Caller holds the user's database row lock when consuming codes."""
import secrets
import time
import pyotp
import base64
import io
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.core.exceptions import ImproperlyConfigured
from .models import RecoveryCode


def cipher():
    key = settings.OXXOM_MFA_KEY
    if not key:
        raise ImproperlyConfigured("Configure OXXOM_MFA_KEY before enabling authentication.")
    return Fernet(key.encode())


def encrypt(secret):
    return cipher().encrypt(secret.encode()).decode()


def decrypt(token):
    return cipher().decrypt(token.encode()).decode()


def provisioning_qr(uri):
    """Render in-process; the TOTP URI never leaves this application."""
    import qrcode
    from qrcode.image.svg import SvgPathImage
    image = qrcode.make(uri, image_factory=SvgPathImage, box_size=6, border=4)
    output = io.BytesIO()
    image.save(output)
    return 'data:image/svg+xml;base64,' + base64.b64encode(output.getvalue()).decode('ascii')


def consume_totp(device, code):
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return False
    totp = pyotp.TOTP(decrypt(device.secret))
    step = int(time.time()) // 30
    for candidate in (step, step - 1, step + 1):
        if candidate > device.last_step and pyotp.utils.strings_equal(totp.at(candidate * 30), code):
            device.last_step = candidate
            device.save(update_fields=["last_step"])
            return True
    return False


def recovery_codes(user):
    RecoveryCode.objects.filter(user=user).delete()
    codes = [secrets.token_hex(8) for _ in range(10)]
    RecoveryCode.objects.bulk_create([RecoveryCode(user=user, code_hash=make_password(code)) for code in codes])
    return codes


def consume_recovery(user, code):
    if len(code) != 16 or any(c not in "0123456789abcdef" for c in code):
        return False
    for record in RecoveryCode.objects.filter(user=user):
        if check_password(code, record.code_hash):
            record.delete()
            return True
    return False
