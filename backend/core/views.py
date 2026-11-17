from django.db import connection, DatabaseError
from django.http import HttpResponse, JsonResponse, FileResponse
from pathlib import Path
from django.views.decorators.http import require_GET, require_safe
from django.views.decorators.cache import never_cache
from django.views.decorators.gzip import gzip_page


@require_safe
@never_cache
def home(request, language='uk'):
    from django.template.loader import render_to_string
    from .seo import context, render_public
    return HttpResponse(render_public(render_to_string('core/landing.html', context(language), request=request), language))


@require_safe
def robots(request):
    from django.conf import settings
    from .seo import ORIGIN
    text = 'User-agent: *\nAllow: /\nSitemap: ' + ORIGIN + '/sitemap.xml\n'
    if not settings.OXXOM_INDEX_PUBLIC:
        text = 'User-agent: *\nDisallow: /\n'
    response = HttpResponse(text, content_type='text/plain; charset=utf-8')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


@require_safe
def sitemap(request):
    from xml.etree.ElementTree import Element, SubElement, tostring
    from .seo import CONTENT_UPDATED, LANGUAGES, ORIGIN
    root = Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    for item in LANGUAGES.values():
        node = SubElement(root, 'url')
        SubElement(node, 'loc').text = ORIGIN + item['path']
        SubElement(node, 'lastmod').text = CONTENT_UPDATED
    response = HttpResponse(tostring(root, encoding='utf-8', xml_declaration=True), content_type='application/xml; charset=utf-8')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


def asset_cache(request, response, name):
    from .seo import fingerprint
    if request.GET.get('v') == fingerprint(name):
        response['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response


@require_safe
def public_brand_asset(request, filename):
    from django.http import Http404
    allowed = {'oxxom-oblik-web.png', 'og-oblik-uk.png', 'og-oblik-en.png', 'og-oblik-ru.png', 'og-oblik-cs.png',
               'favicon-oblik.png', 'apple-touch-oblik.png', 'favicon.ico'}
    if filename not in allowed:
        raise Http404
    path = Path(__file__).parent / 'static/core' / filename
    response = FileResponse(path.open('rb'), content_type='image/x-icon' if filename.endswith('.ico') else 'image/png')
    response['Cache-Control'] = 'public, max-age=86400'
    return asset_cache(request, response, filename)


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


@require_safe
@gzip_page
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


@require_safe
@gzip_page
def landing_asset(request, filename):
    from django.http import Http404
    content_types = {'landing.css': 'text/css', 'landing.js': 'text/javascript', 'public-preferences.js': 'text/javascript',
                     'demo-booking.css': 'text/css', 'demo-booking.js': 'text/javascript'}
    if filename not in content_types:
        raise Http404
    asset = Path(__file__).parent / 'static' / 'core' / filename
    response = FileResponse(asset.open('rb'), content_type=content_types[filename])
    response['Cache-Control'] = 'public, max-age=3600'
    return asset_cache(request, response, filename)


@require_safe
@gzip_page
def ui_asset(request, filename):
    from django.http import Http404
    content_types = {'preferences.js': 'text/javascript', 'translations.js': 'text/javascript', 'preferences.css': 'text/css'}
    if filename not in content_types:
        raise Http404
    asset = Path(__file__).parent / 'static' / 'core' / 'ui' / filename
    response = FileResponse(asset.open('rb'), content_type=content_types[filename])
    response['Cache-Control'] = 'public, max-age=3600'
    return asset_cache(request, response, 'ui/' + filename)
