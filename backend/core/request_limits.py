"""Reject excessive WSGI request bodies before CSRF parses multipart uploads.

This bounds application parsing, not bytes received by the reverse proxy.
The deployment must also enforce edge limits and timeouts. ASGI buffering is
outside this middleware's protection; production currently uses Gunicorn WSGI.
"""
import re

from django.http import JsonResponse


API_LIMIT = 128 * 1024
AUTH_LIMIT = 8 * 1024
IMPORT_LIMIT = 1024 * 1024 + 16 * 1024  # One 1 MiB CSV plus multipart framing.
IMPORT_PREVIEW = re.compile(
    r'/api/v1/organizations/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
    r'[0-9a-f]{4}-[0-9a-f]{12}/imports/'
    r'(?:units|products|counterparties|warehouses)/preview\Z'
)


class RequestBodyLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            return self.get_response(request)
        # Preserve the stricter fixture-only demo contract and its error codes.
        if request.path == '/api/public/demo-requests':
            return self.get_response(request)
        length = request.META.get('CONTENT_LENGTH', '')
        status, error = None, None
        if request.META.get('HTTP_TRANSFER_ENCODING'):
            status, error = 411, 'content_length_required'
        elif not length:
            # WSGIRequest exposes a zero-length stream when length is absent.
            # An empty fetch POST (logout/setup) must remain valid.
            if request.content_type:
                status, error = 411, 'content_length_required'
        elif not length.isascii() or not length.isdecimal() or len(length) > 10:
            status, error = 400, 'invalid_content_length'
        else:
            limit = (AUTH_LIMIT if request.path.startswith('/api/v1/auth/')
                     else IMPORT_LIMIT if IMPORT_PREVIEW.fullmatch(request.path)
                     else API_LIMIT)
            if int(length) > limit:
                status, error = 413, 'payload_too_large'
        if status:
            response = JsonResponse({'error': error}, status=status)
            response['Cache-Control'] = 'no-store, private'
            response['X-Robots-Tag'] = 'noindex, nofollow'
            return response
        return self.get_response(request)
