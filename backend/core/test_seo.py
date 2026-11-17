import gzip
import json
import re
import struct
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from django.test import SimpleTestCase, override_settings
from .seo import LANGUAGES, ORIGIN, context, render_public


class PublicPage(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.links, self.meta, self.scripts, self.headings = [], {}, {}, []
        self.stack, self.active_script = [], None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'link':
            self.links.append(values)
        if tag == 'meta':
            self.meta[values.get('name', values.get('property'))] = values.get('content')
        if tag in ('h1', 'h2', 'h3'):
            self.headings.append([tag, ''])
        if tag == 'script':
            self.active_script = values.get('id') or values.get('type')
            self.scripts[self.active_script] = ''
        if tag not in ('meta', 'link', 'img', 'br', 'input', 'path', 'rect', 'circle', 'line', 'polyline'):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active_script = None
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_data(self, value):
        if self.active_script:
            self.scripts[self.active_script] += value
        elif any(tag in ('h1', 'h2', 'h3') for tag in self.stack):
            self.headings[-1][1] += value


@override_settings(OXXOM_INDEX_PUBLIC=True)
class PublicSEOTests(SimpleTestCase):
    def test_each_language_is_server_rendered_and_overrides_conflicting_cookie(self):
        for code, item in LANGUAGES.items():
            with self.subTest(language=code):
                self.client.cookies['oxxom_language'] = 'ru' if code != 'ru' else 'en'
                response = self.client.get(item['path'] + '?utm_source=qa&email=private@example.invalid')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Language'], code)
                self.assertContains(response, 'lang="' + code + '"')
                self.assertContains(response, item['title'])
                self.assertNotIn('X-Robots-Tag', response)
                self.assertNotIn('Cookie', response.get('Vary', ''))
                self.assertEqual(response.cookies['oxxom_language'].value, code)
                page = PublicPage(response.content.decode())
                canonical = [link['href'] for link in page.links if link.get('rel') == 'canonical']
                self.assertEqual(canonical, [ORIGIN + item['path']])
                alternatives = {link['hreflang']: link['href'] for link in page.links if link.get('rel') == 'alternate'}
                self.assertEqual(alternatives, {**{lang: ORIGIN + value['path'] for lang, value in LANGUAGES.items()}, 'x-default': ORIGIN + '/'})
                self.assertEqual(page.meta['description'], item['description'])
                self.assertEqual(page.meta['og:url'], ORIGIN + item['path'])
                self.assertEqual(page.meta['og:locale'], item['og'])
                self.assertEqual(page.meta['twitter:card'], 'summary_large_image')
                self.assertNotContains(response, 'private@example.invalid')
                self.assertNotContains(response, 'translations.js')
                self.assertEqual(sum(tag == 'h1' for tag, _ in page.headings), 1)
                self.assertTrue(all(not value.rstrip().endswith('.') for _, value in page.headings))
                data = json.loads(page.scripts['landing-data'])
                self.assertEqual(data['language'], code)
                self.assertEqual(data['states']['overview']['values'], [2480000, 864000, 1286])
                graph = json.loads(page.scripts['application/ld+json'])['@graph']
                self.assertEqual({entry['@type'] for entry in graph}, {'Organization', 'WebSite'})
                self.assertFalse(any('offers' in entry or 'aggregateRating' in entry for entry in graph))

    def test_fixed_canonical_does_not_trust_alternate_host_or_query_parameters(self):
        with override_settings(ALLOWED_HOSTS=['other.example.invalid']):
            response = self.client.get('/en/?next=https://untrusted.invalid/', HTTP_HOST='other.example.invalid')
        self.assertContains(response, 'rel="canonical" href="' + ORIGIN + '/en/"')
        self.assertNotContains(response, 'other.example.invalid')
        self.assertNotContains(response, 'untrusted.invalid')

    def test_sitemap_is_an_explicit_public_allowlist_and_lastmod_is_stable(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('application/xml'))
        root = ET.fromstring(response.content)
        ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        self.assertEqual({item.text for item in root.findall('s:url/s:loc', ns)}, {ORIGIN + item['path'] for item in LANGUAGES.values()})
        self.assertTrue(all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', item.text) for item in root.findall('s:url/s:lastmod', ns)))
        self.client.cookies['oxxom_language'] = 'cs'
        self.assertEqual(self.client.get('/sitemap.xml').content, response.content)
        robots = self.client.get('/robots.txt')
        self.assertContains(robots, 'Sitemap: ' + ORIGIN + '/sitemap.xml')
        self.assertNotContains(robots, 'Disallow: /login')

    def test_non_public_responses_are_noindex_without_weakening_authorization(self):
        for path, statuses in [('/login/', (200,)), ('/app/', (302,)), ('/documents/', (302,)),
                               ('/api/v1/organizations', (401,)), ('/api/v1/health', (200,)),
                               ('/uk/', (404,)), ('/de/', (404,)), ('/missing/', (404,))]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(response.status_code, statuses)
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')

    def test_staging_cannot_opt_into_indexing_by_request_parameters(self):
        with override_settings(OXXOM_INDEX_PUBLIC=False):
            for path in ('/', '/en/', '/robots.txt', '/sitemap.xml'):
                self.assertEqual(self.client.get(path + '?index=true')['X-Robots-Tag'], 'noindex, nofollow')
            self.assertContains(self.client.get('/robots.txt'), 'Disallow: /')

    def test_public_assets_have_real_dimensions_and_content_versioned_cache(self):
        for code in LANGUAGES:
            assets = context(code)['public_assets']
            for key in ('logo', 'og'):
                response = self.client.get(assets[key])
                data = b''.join(response.streaming_content)
                self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
                self.assertIn('immutable', response['Cache-Control'])
                self.assertNotIn('X-Robots-Tag', response)
                if key == 'og':
                    self.assertEqual(struct.unpack('>II', data[16:24]), (1200, 630))
                else:
                    self.assertLess(len(data), 60 * 1024)
        assets = context('en')['public_assets']
        css = self.client.get(assets['css'], HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(css['Content-Encoding'], 'gzip')
        self.assertIn(b'.hero', gzip.decompress(b''.join(css.streaming_content)))
        response = self.client.get('/assets/landing/landing.css?v=wrong')
        self.assertNotIn('immutable', response['Cache-Control'])
        response.close()
        for path in ('/assets/brand/secrets.png', '/assets/landing/settings.py'):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_public_head_requests_and_path_redirects(self):
        for path in ('/', '/en/', '/ru/', '/cs/', '/robots.txt', '/sitemap.xml'):
            self.assertEqual(self.client.head(path).status_code, 200)
        self.assertRedirects(self.client.get('/en'), '/en/', status_code=301, fetch_redirect_response=False)

    def test_badge_favicon_is_shared_by_public_auth_workspace_and_print_templates(self):
        from django.template.loader import render_to_string
        for name in ('core/landing.html', 'accounts/login.html', 'core/dashboard.html',
                     'accounts/workspace.html', 'catalog/index.html', 'documents/index.html',
                     'inventory/index.html', 'operations/index.html', 'posting/index.html',
                     'payments/index.html', 'reports/index.html', 'catalog_imports/index.html',
                     'printing/settings.html', 'printing/document.html', 'printing/error.html'):
            with self.subTest(template=name):
                markup = render_to_string(name, {**context('uk'), 'ui_workspace_name': 'QA',
                                                'workspace': {'name': 'QA', 'role': 'owner', 'navigation': []}})
                icons = [link for link in PublicPage(markup).links if link.get('rel') == 'icon']
                self.assertEqual(len(icons), 1)
                self.assertTrue(icons[0]['href'].startswith('/assets/brand/favicon-oblik.png?'))
                self.assertIn('apple-touch-icon', markup)
        icon = self.client.get('/favicon.ico')
        data = b''.join(icon.streaming_content)
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(icon['Content-Type'], 'image/x-icon')
        self.assertEqual(data[:6], b'\x00\x00\x01\x00\x04\x00')
        self.assertNotIn('X-Robots-Tag', icon)
        for name, dimensions in [('favicon-oblik.png', (256, 256)), ('apple-touch-oblik.png', (180, 180))]:
            response = self.client.get('/assets/brand/' + name)
            payload = b''.join(response.streaming_content)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(struct.unpack('>II', payload[16:24]), dimensions)

    def test_public_renderer_preserves_json_and_native_language_names(self):
        markup = '<h2>Більше порядку.</h2><p>Більше порядку.</p><script>{"value":"Більше порядку."}</script><nav data-ui-native aria-label="Мова інтерфейсу">Українська</nav>'
        result = render_public(markup, 'en')
        self.assertIn('<h2>More order</h2>', result)
        self.assertIn('<p>More order.</p>', result)
        self.assertIn('{"value":"Більше порядку."}', result)
        self.assertIn('aria-label="Interface language">Українська', result)
