"""Account-wide session revocation without enumerating other users' sessions."""
import json
import logging
from uuid import uuid4

from django.contrib.auth import HASH_SESSION_KEY, update_session_auth_hash
from django.db import transaction
from django.utils.crypto import constant_time_compare

from .models import User


def locked_session_user(request):
    """Caller holds an atomic block; reject requests overtaken by revocation.

    AuthenticationMiddleware may have loaded the user before another request
    rotated the security stamp. Recheck the session hash after the User row lock
    before consuming a factor or preserving a session under the new stamp.
    """
    user = User.objects.select_for_update().filter(pk=request.user.pk, is_active=True).first()
    if user is None or not constant_time_compare(
            user.get_session_auth_hash(), request.session.get(HASH_SESSION_KEY, '')):
        return None
    return user


def revoke_other_sessions(request, user):
    # Django rejects old hashes on their next request, including pending login
    # challenges. Keep the current verified MFA start/absolute expiry unchanged.
    user.security_stamp = uuid4()
    user.save(update_fields=['security_stamp'])
    update_session_auth_hash(request, user)
    request.user = user


def security_event(action, user, result):
    # Structured metadata only. No code, password, email, cookie, hash or IP.
    message = json.dumps({'event': action, 'user_id': str(user.pk), 'result': result},
                         sort_keys=True, separators=(',', ':'))
    transaction.on_commit(lambda: logging.getLogger('oxxom.security').info(message))
