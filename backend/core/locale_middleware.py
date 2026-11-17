"""Localise authored API metadata and validation, preserving business payloads."""
import json
from django.http import JsonResponse
from django.utils import translation
from django.utils.cache import patch_vary_headers
from .i18n import COOKIE, language, translate


class InterfaceLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/assets/'):
            return self.get_response(request)
        from .seo import PUBLIC_PATHS
        public_language = PUBLIC_PATHS.get(request.path)
        code = public_language or language(request.COOKIES.get(COOKIE))
        with translation.override(code):
            response = self.get_response(request)
            if isinstance(response, JsonResponse):
                data = json.loads(response.content)
                self.errors(data, code)
                if request.path.endswith('/workspace-overview'):
                    for card in data.get('cards', []):
                        for key in ('label', 'create_label'):
                            if key in card:
                                card[key] = translate(card[key], code)
                if request.path.endswith('/import-options'):
                    for item in data.get('kinds', []):
                        for key in ('title', 'note'):
                            if key in item:
                                item[key] = translate(item[key], code)
                if request.path.endswith('/report-options'):
                    for report in data.get('reports', []):
                        self.report(report, code)
                elif '/reports/' in request.path:
                    self.report(data, code)
                    for row in data.get('items', []):
                        if 'action' in row:
                            row['action'] = translate(row['action'], code)
                response.content = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
                if 'Content-Length' in response:
                    response['Content-Length'] = len(response.content)
            if isinstance(response, JsonResponse) or response.get('Content-Type', '').startswith('text/html'):
                if response.status_code < 400 or not response.has_header('Content-Language'):
                    response['Content-Language'] = 'uk' if '/print/' in request.path and response.status_code == 200 else code
                if public_language is None:
                    patch_vary_headers(response, ('Cookie',))
            if public_language is not None and response.status_code == 200:
                from django.conf import settings
                response.set_cookie(COOKIE, code, max_age=31536000, samesite='Lax', secure=settings.SESSION_COOKIE_SECURE)
        return response

    @staticmethod
    def report(data, code):
        for key in ('title', 'note'):
            if key in data:
                data[key] = translate(data[key], code)
        for column in data.get('columns', []):
            column['label'] = translate(column['label'], code)

    @classmethod
    def errors(cls, data, code):
        # Only designated validation fields are UI. All other strings, including
        # data snapshots and user-entered names/notes, retain their original values.
        if not isinstance(data, dict):
            return
        for key in ('error', 'message'):
            if isinstance(data.get(key), str):
                data[key] = translate(data[key], code)
        for key in ('fields', 'errors', 'warnings', 'issues'):
            if key in data:
                data[key] = cls.validation(data[key], code)
        if isinstance(data.get('batch'), dict):
            cls.errors(data['batch'], code)
        for key in ('rows', 'items'):
            if isinstance(data.get(key), list):
                for row in data[key]:
                    if isinstance(row, dict):
                        cls.errors(row, code)

    @classmethod
    def validation(cls, value, code):
        if isinstance(value, str):
            return translate(value, code)
        if isinstance(value, list):
            return [cls.validation(item, code) for item in value]
        if isinstance(value, dict):
            return {key: cls.validation(item, code) if key != 'code' else item for key, item in value.items()}
        return value
