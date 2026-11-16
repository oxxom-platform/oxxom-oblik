from html.parser import HTMLParser
from urllib.parse import urlsplit
from django.test import SimpleTestCase


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        if tag == 'a' and 'href' in attrs:
            self.links.append(attrs['href'])


class LandingTests(SimpleTestCase):
    # Public presentation must render even with all database access forbidden.
    def test_public_home_is_database_independent_with_working_internal_destinations(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertContains(response, 'lang="uk"')
        self.assertContains(response, 'Ілюстративні дані')
        self.assertContains(response, '/assets/brand/oxxom-oblik.svg?v=20261115-original-aligned')
        self.assertNotContains(response, 'workspace-config')
        self.assertNotContains(response, 'id="organization"')
        self.assertNotContains(response, 'id="motion-toggle"')
        parser = PageLinks()
        parser.feed(response.content.decode())
        self.assertIn('/login/', parser.links)
        for href in parser.links:
            if href.startswith('#'):
                self.assertIn(href[1:], parser.ids)
            else:
                self.assertIn(href, ('/', '/login/'))
                self.assertFalse(urlsplit(href).netloc)
        self.assertEqual(self.client.post('/').status_code, 405)

    def test_only_explicit_landing_assets_can_be_served(self):
        for name, content_type in [('landing.css', 'text/css'), ('landing.js', 'text/javascript')]:
            response = self.client.get('/assets/landing/' + name)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response['Content-Type'].startswith(content_type))
            self.assertGreater(len(b''.join(response.streaming_content)), 100)
        self.assertEqual(self.client.get('/assets/landing/settings.py').status_code, 404)
        self.assertEqual(self.client.get('/assets/landing/mfa.key').status_code, 404)

    def test_home_parameters_never_expose_business_or_session_context(self):
        response = self.client.get('/?organization=private-org&email=private-person@example.test')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'private-org')
        self.assertNotContains(response, 'private-person')
        self.assertNotContains(response, '/api/v1/organizations')

    def test_public_preference_assets_are_allowlisted_and_menu_is_icon_only(self):
        for filename in ('preferences.js', 'translations.js', 'preferences.css'):
            response = self.client.get('/assets/ui/' + filename)
            self.assertEqual(response.status_code, 200)
            self.assertGreater(len(b''.join(response.streaming_content)), 100)
        for filename in ('settings.py', 'mfa.key', 'translations.json'):
            self.assertEqual(self.client.get('/assets/ui/' + filename).status_code, 404)
        response = self.client.get('/')
        self.assertContains(response, 'id="mobile-menu-panel"')
        self.assertNotContains(response, 'data-theme-choice')
        self.assertContains(response, 'class="language-switch"')
        for language in ('uk', 'en', 'ru', 'cs'):
            self.assertContains(response, f'value="{language}" lang="{language}"')
        self.assertNotContains(response, '>Меню <')
