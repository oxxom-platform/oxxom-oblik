"""Bound the synthetic public intake before CSRF can parse uploads."""
from django.http import JsonResponse


class DemoBodyLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/api/public/demo-requests' and request.method == 'POST':
            status, error = None, None
            if request.content_type != 'application/x-www-form-urlencoded':
                status, error = 415, 'unsupported_media_type'
            else:
                length = request.META.get('CONTENT_LENGTH', '')
                if (not length.isascii() or not length.isdecimal()
                        or len(length) > 8 or int(length) > 4096):
                    status, error = 413, 'payload_too_large'
            if status:
                response = JsonResponse({'error': error}, status=status)
                response['Cache-Control'] = 'no-store, private'
                response['X-Robots-Tag'] = 'noindex, nofollow'
                return response
        return self.get_response(request)
