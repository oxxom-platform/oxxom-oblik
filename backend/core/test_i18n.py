import json
import re
import time
from pathlib import Path
from django.test import TestCase, SimpleTestCase, RequestFactory
from django.http import JsonResponse
from django.template.loader import render_to_string
from accounts.models import User, Organization, Membership
from catalog.models import Counterparty
from .i18n import catalog, translate
from .locale_middleware import InterfaceLanguageMiddleware


class InterfaceCatalogTests(SimpleTestCase):
    def test_localised_validation_preserves_interpolated_business_values(self):
        self.assertEqual(translate('Недостатній залишок: Огляд. Доступно 1, потрібно 2.', 'ru'),
                         'Недостаточный остаток: Огляд. Доступно 1, требуется 2.')
        self.assertEqual(translate('CSV-рядок 17: кількість колонок не відповідає заголовку.', 'en'),
                         'CSV row 17: column count does not match the header.')
        self.assertEqual(translate('Заголовок не відповідає шаблону: name;symbol', 'ru'),
                         'Заголовок не соответствует шаблону: name;symbol')
        for row in catalog().values():
            self.assertEqual(len(row), 3)
            self.assertTrue(all(row))

    def test_json_localisation_only_changes_authored_metadata_and_validation(self):
        original = {'name': 'Огляд', 'note': 'Зберегти', 'code': 'РЛ-2026-01',
                    'error': 'Недостатньо прав доступу.', 'items': [{'name': 'Організація', 'amount': '123.45'}]}
        request = RequestFactory().get('/api/v1/example')
        request.COOKIES['oxxom_language'] = 'ru'
        response = InterfaceLanguageMiddleware(lambda r: JsonResponse(original))(request)
        actual = json.loads(response.content)
        self.assertEqual(actual['error'], 'Недостаточно прав доступа.')
        for key in ('name', 'note', 'code', 'items'):
            self.assertEqual(actual[key], original[key])
        self.assertEqual(response['Content-Language'], 'ru')

    def test_browser_and_server_use_identical_complete_catalogues(self):
        script = (Path(__file__).parent / 'static/core/ui/translations.js').read_text()
        actual = json.loads(script.split('=', 1)[1].strip().rstrip(';'))
        self.assertEqual(actual, catalog())


class WorkspaceLanguageTests(TestCase):
    pages = ('/app/', '/team/', '/catalog/', '/documents/', '/inventory/', '/operations/',
             '/stock/', '/payments/', '/reports/', '/organization-details/', '/imports/')

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('locale-owner@example.test', 'Testing-only-password-734!')
        cls.org = Organization.objects.create(name='Налаштування')
        Membership.objects.create(user=cls.user, organization=cls.org, role='owner')
        cls.cp = Counterparty.objects.create(organization=cls.org, name='Зберегти', kind='company', note='Огляд')

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()

    def test_all_workspace_pages_follow_the_landing_language_cookie(self):
        for code in ('uk', 'en', 'ru', 'cs'):
            self.client.cookies['oxxom_language'] = code
            for path in self.pages:
                with self.subTest(language=code, path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, f'lang="{code}"')
                    self.assertContains(response, 'Налаштування')  # organisation name is data
                    self.assertContains(response, translate('Безпека та резервні коди', code))
                    self.assertContains(response, 'data-language-select')
                    self.assertNotContains(response, 'data-theme-choice')
                    self.assertIn('no-store', response['Cache-Control'])

    def test_catalogue_data_and_machine_identifiers_are_not_translated(self):
        for code in ('en', 'ru', 'cs'):
            self.client.cookies['oxxom_language'] = code
            data = self.client.get(f'/api/v1/organizations/{self.org.pk}/catalog/counterparties').json()
            self.assertEqual(data['items'][0]['name'], 'Зберегти')
            self.assertEqual(data['items'][0]['note'], 'Огляд')
            self.assertEqual(data['items'][0]['kind'], 'company')
            self.assertEqual(self.client.get('/api/v1/organizations').json()['organizations'][0]['name'], 'Налаштування')

    def test_dynamic_dashboard_and_report_labels_use_the_same_language(self):
        self.client.cookies['oxxom_language'] = 'ru'
        cards = self.client.get(f'/api/v1/organizations/{self.org.pk}/workspace-overview').json()['cards']
        self.assertIn('Продажи', [card['label'] for card in cards])
        self.assertIn('Новая реализация', [card['create_label'] for card in cards])
        options = self.client.get(f'/api/v1/organizations/{self.org.pk}/report-options').json()['reports']
        self.assertTrue(all(item['title'] == translate(item['title'], 'ru') for item in options))
        self.assertIn('Content-Language', self.client.get('/reports/'))

    def test_password_validation_uses_selected_language_and_does_not_create_account(self):
        self.client.cookies['oxxom_language'] = 'ru'
        response = self.client.post(f'/api/v1/organizations/{self.org.pk}/members/add', {
            'email': 'new-locale@example.test', 'role': 'viewer', 'password': '123',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'Проверьте начальный пароль нового аккаунта.')
        self.assertTrue(response.json()['fields']['password'])
        self.assertFalse(User.objects.filter(email='new-locale@example.test').exists())

    def test_unsupported_language_falls_back_without_reflecting_cookie(self):
        self.client.cookies['oxxom_language'] = '<script>invalid</script>'
        response = self.client.get('/app/')
        self.assertContains(response, 'lang="uk"')
        self.assertNotContains(response, '<script>invalid</script>')

    def test_import_metadata_and_preview_errors_are_localised_without_changing_csv_fields(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.cookies['oxxom_language'] = 'ru'
        options = self.client.get(f'/api/v1/organizations/{self.org.pk}/import-options').json()['kinds']
        units = next(item for item in options if item['kind'] == 'units')
        self.assertEqual(units['title'], 'Единицы измерения')
        self.assertEqual(units['fields'], ['name', 'symbol'])
        self.assertIn('название', units['note'])
        upload = SimpleUploadedFile('validation.csv', b'name;symbol\nTest;\n', content_type='text/csv')
        response = self.client.post(f'/api/v1/organizations/{self.org.pk}/imports/units/preview', {'file': upload})
        self.assertEqual(response.status_code, 201)
        rows = response.json()['batch']['rows']
        self.assertEqual(rows[0]['input']['name'], 'Test')
        self.assertEqual(rows[0]['status'], 'error')
        self.assertNotIn('Обов’язкове поле.', ' '.join(rows[0]['errors']))
