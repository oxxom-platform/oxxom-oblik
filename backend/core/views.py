from django.db import connection, DatabaseError
from django.http import HttpResponse, JsonResponse, FileResponse
from pathlib import Path
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache


@require_GET
@never_cache
def home(request):
    from django.shortcuts import render
    return render(request, 'core/landing.html')


@require_GET
@never_cache
def health(request):
    return JsonResponse({"status": "ok", "service": "oxxom-oblik", "stage": "core"})


@require_GET
@never_cache
def ready(request):
    try:
        from django.db.migrations.executor import MigrationExecutor
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            return JsonResponse({"status": "not_ready"}, status=503)
    except DatabaseError:
        return JsonResponse({"status": "not_ready"}, status=503)
    return JsonResponse({"status": "ready"})


@require_GET
def brand_logo(request):
    path = Path(__file__).parent / 'static' / 'core' / 'oxxom-oblik-transparent.png'
    response = FileResponse(path.open('rb'), content_type='image/png')
    response['Cache-Control'] = 'public, max-age=86400'
    return response


@require_GET
def brand_logo_svg(request):
    path = Path(__file__).parent / 'static' / 'core' / 'oxxom-oblik-aligned.svg'
    response = FileResponse(path.open('rb'), content_type='image/svg+xml')
    response['Cache-Control'] = 'public, max-age=86400'
    response['Content-Security-Policy'] = "default-src 'none'; img-src data:; sandbox"
    return response


@require_GET
def workspace_asset(request, filename):
    from django.http import Http404
    content_types = {'workspace.css': 'text/css', 'workspace.js': 'text/javascript'}
    if filename not in content_types:
        raise Http404
    asset = Path(__file__).parent / 'static' / 'core' / filename
    response = FileResponse(asset.open('rb'), content_type=content_types[filename])
    response['Cache-Control'] = 'public, max-age=3600'
    return response


@require_GET
def landing_asset(request, filename):
    from django.http import Http404
    content_types = {'landing.css': 'text/css', 'landing.js': 'text/javascript'}
    if filename not in content_types:
        raise Http404
    asset = Path(__file__).parent / 'static' / 'core' / filename
    response = FileResponse(asset.open('rb'), content_type=content_types[filename])
    response['Cache-Control'] = 'public, max-age=3600'
    return response


@require_GET
def ui_asset(request, filename):
    from django.http import Http404
    content_types = {'preferences.js': 'text/javascript', 'translations.js': 'text/javascript', 'preferences.css': 'text/css'}
    if filename not in content_types:
        raise Http404
    asset = Path(__file__).parent / 'static' / 'core' / 'ui' / filename
    response = FileResponse(asset.open('rb'), content_type=content_types[filename])
    response['Cache-Control'] = 'public, max-age=3600'
    return response
