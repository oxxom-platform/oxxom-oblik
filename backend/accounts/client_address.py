"""One trusted NPM hop. Never use client-supplied leftmost forwarding values."""
from functools import wraps
from ipaddress import ip_address

from django.conf import settings
from django.http import JsonResponse


def canonical_address(value):
    if not isinstance(value, str) or not value or '%' in value:
        raise ValueError('Invalid client address')
    address = ip_address(value.strip())
    return str(getattr(address, 'ipv4_mapped', None) or address)


def client_address(request):
    peer = canonical_address(request.META.get('REMOTE_ADDR', ''))
    if peer not in settings.OXXOM_AUTH_PROXY_IPS:
        return peer
    # NPM appends its TCP client and overwrites X-Real-IP. A forged prefix
    # cannot select the throttle key. This is deliberately a single-hop policy.
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if len(forwarded) > 2048 or not forwarded:
        raise ValueError('Missing or invalid proxy metadata')
    candidate = canonical_address(forwarded.rsplit(',', 1)[-1])
    if candidate != canonical_address(request.META.get('HTTP_X_REAL_IP', '')):
        raise ValueError('Inconsistent proxy metadata')
    return candidate


def validated_auth_source(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            request.auth_client_address = client_address(request)
        except ValueError:
            return JsonResponse({'error': 'invalid_auth_source'}, status=400)
        return view(request, *args, **kwargs)
    return wrapped
