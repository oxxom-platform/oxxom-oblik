import time
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, Client
from accounts.models import User, Organization, Membership
from .models import Unit, Product, Counterparty, Warehouse, CatalogEvent


class CatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('catalog@example.com', 'Tests-Only-Password-782!')
        cls.org = Organization.objects.create(name='A')
        cls.other = Organization.objects.create(name='B')
        cls.member = Membership.objects.create(user=cls.user, organization=cls.org, role='owner')
        cls.unit = Unit.objects.create(organization=cls.org, name='Штука', symbol='шт')
        cls.foreign_unit = Unit.objects.create(organization=cls.other, name='Foreign', symbol='x')
        cls.product = Product.objects.create(organization=cls.org, name='Товар', kind='goods', unit=cls.unit, price='100.00')
        cls.foreign_product = Product.objects.create(organization=cls.other, name='Secret', kind='goods', unit=cls.foreign_unit, price='900.00')

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()
        self.base = f'/api/v1/organizations/{self.org.pk}/catalog/'

    def product_data(self, **extra):
        return {'name': 'Service', 'kind': 'service', 'unit': str(self.unit.pk), 'price': '1500.00', **extra}

    def test_full_mfa_required(self):
        client = Client()
        self.assertEqual(client.get('/catalog/').status_code, 302)
        client.force_login(self.user)
        self.assertEqual(client.get(self.base + 'products').status_code, 401)
        self.assertEqual(client.post(self.base + 'products/add', self.product_data()).status_code, 401)

    def test_catalog_page_and_scoped_list(self):
        self.assertEqual(self.client.get('/catalog/').status_code, 200)
        response = self.client.get(self.base + 'products')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual([i['name'] for i in response.json()['items']], ['Товар'])
        self.assertEqual(response.json()['items'][0]['price'], '100.00')

    def test_create_all_reference_types_and_audit(self):
        cases = [('units', {'name': 'Година', 'symbol': 'год'}), ('products', self.product_data()), ('counterparties', {'name': 'Клієнт', 'kind': 'company', 'code': '12345678'}), ('warehouses', {'name': 'Основний'})]
        for kind, data in cases:
            with self.subTest(kind=kind):
                response = self.client.post(self.base + kind + '/add', data)
                self.assertEqual(response.status_code, 201, response.content)
                item = response.json()['item']
                self.assertEqual(item['version'], 1)
                self.assertTrue(item['is_active'])
        self.assertEqual(CatalogEvent.objects.count(), 4)
        self.assertTrue(all(e.organization_id == self.org.pk and e.actor_id == self.user.pk for e in CatalogEvent.objects.all()))
        self.assertEqual(Product.objects.get(name='Service').price, Decimal('1500.00'))

    def test_foreign_organization_and_known_uuid_denied(self):
        foreign_base = f'/api/v1/organizations/{self.other.pk}/catalog/'
        self.assertEqual(self.client.get(foreign_base + 'products').status_code, 403)
        self.assertEqual(self.client.post(foreign_base + 'warehouses/add', {'name': 'Intrusion'}).status_code, 403)
        response = self.client.post(self.base + 'products/' + str(self.foreign_product.pk), self.product_data(version=1))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.post(self.base + 'products/' + str(self.foreign_product.pk) + '/state', {'version': 1, 'is_active': 'false'}).status_code, 404)
        self.foreign_product.refresh_from_db()
        self.assertEqual(self.foreign_product.name, 'Secret')

    def test_foreign_and_archived_units_rejected_when_creating_product(self):
        for unit in [self.foreign_unit, self.unit]:
            if unit == self.unit:
                Unit.objects.filter(pk=unit.pk).update(is_active=False)
            response = self.client.post(self.base + 'products/add', self.product_data(unit=str(unit.pk)))
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Product.objects.filter(organization=self.org).count(), 1)

    def test_unit_is_required_and_does_not_accept_manual_text(self):
        for unit in ['', 'година']:
            self.assertEqual(self.client.post(self.base + 'products/add', self.product_data(unit=unit)).status_code, 400)

    def test_prices_reject_negative_nonfinite_and_excess_precision(self):
        for price in ['-1', 'NaN', 'Infinity', '0.001', '1000000000000.00', 'abc']:
            with self.subTest(price=price):
                self.assertEqual(self.client.post(self.base + 'products/add', self.product_data(price=price)).status_code, 400)
        self.assertEqual(self.client.post(self.base + 'products/add', self.product_data(price='0.00')).status_code, 201)

    def test_invalid_fields_fail_without_partial_records(self):
        for data in [{'name': ' ', 'kind': 'company'}, {'name': 'A', 'kind': 'invalid'}, {'name': 'A', 'kind': 'person', 'email': 'invalid'}]:
            self.assertEqual(self.client.post(self.base + 'counterparties/add', data).status_code, 400)
        self.assertFalse(Counterparty.objects.exists())
        self.assertFalse(CatalogEvent.objects.exists())

    def test_organization_and_status_cannot_be_injected_on_creation(self):
        response = self.client.post(self.base + 'products/add', self.product_data(organization=str(self.other.pk), is_active='false', version=900))
        self.assertEqual(response.status_code, 201)
        product = Product.objects.get(pk=response.json()['item']['id'])
        self.assertEqual(product.organization, self.org)
        self.assertTrue(product.is_active)
        self.assertEqual(product.version, 1)

    def test_update_requires_current_version(self):
        url = self.base + 'products/' + str(self.product.pk)
        self.assertEqual(self.client.post(url, self.product_data()).status_code, 400)
        response = self.client.post(url, self.product_data(version=1))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['item']['version'], 2)
        response = self.client.post(url, self.product_data(version=1, price='5.00'))
        self.assertEqual(response.status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('1500.00'))
        self.assertEqual(CatalogEvent.objects.count(), 1)

    def test_archiving_keeps_relations_and_excludes_from_default_list(self):
        url = self.base + 'units/' + str(self.unit.pk) + '/state'
        self.assertEqual(self.client.post(url, {'version': 1, 'is_active': 'false'}).status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.unit_id, self.unit.pk)
        self.assertEqual(self.client.get(self.base + 'units').json()['total'], 0)
        self.assertEqual(self.client.get(self.base + 'units?status=archived').json()['total'], 1)
        # Editing an existing product may retain its previously assigned unit.
        response = self.client.post(self.base + 'products/' + str(self.product.pk), self.product_data(version=1))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post(url, {'version': 2, 'is_active': 'true'}).status_code, 200)
        self.assertEqual(self.client.get(self.base + 'units').json()['total'], 1)

    def test_archive_rejects_stale_version_and_invalid_state(self):
        url = self.base + 'products/' + str(self.product.pk) + '/state'
        self.assertEqual(self.client.post(url, {'version': 1, 'is_active': 'bad'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'version': 0, 'is_active': 'false'}).status_code, 409)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)

    def test_role_permissions_and_price_redaction(self):
        for role in ['accountant', 'manager', 'storekeeper', 'viewer']:
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            response = self.client.get(self.base + 'products')
            self.assertEqual(response.status_code, 200)
            self.assertEqual('price' in response.json()['items'][0], role != 'storekeeper')
            response = self.client.post(self.base + 'products/add', self.product_data())
            self.assertEqual(response.status_code, 201 if role == 'accountant' else 403)
            response = self.client.post(self.base + 'counterparties/add', {'name': 'A', 'kind': 'person'})
            self.assertEqual(response.status_code, 201 if role in ['accountant', 'manager'] else 403)
            response = self.client.post(self.base + 'warehouses/add', {'name': 'A'})
            self.assertEqual(response.status_code, 201 if role in ['accountant', 'storekeeper'] else 403)
        Membership.objects.filter(pk=self.member.pk).update(role='storekeeper')
        self.assertEqual(self.client.get(self.base + 'counterparties').status_code, 403)

    def test_revoked_membership_and_inactive_org_deny_access(self):
        Membership.objects.filter(pk=self.member.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.base + 'products').status_code, 403)
        Membership.objects.filter(pk=self.member.pk).update(is_active=True)
        Organization.objects.filter(pk=self.org.pk).update(is_active=False)
        self.assertEqual(self.client.post(self.base + 'units/add', {'name': 'A', 'symbol': 'a'}).status_code, 403)

    def test_search_and_pagination_and_invalid_kind(self):
        for i in range(52):
            Warehouse.objects.create(organization=self.org, name=f'Store {i:02}')
        response = self.client.get(self.base + 'warehouses?page=2').json()
        self.assertEqual(response['total'], 52)
        self.assertEqual(len(response['items']), 2)
        self.assertEqual(self.client.get(self.base + 'warehouses?q=Store%2001').json()['total'], 1)
        self.assertEqual(self.client.get(self.base + 'invalid').status_code, 404)
        self.assertEqual(self.client.get(self.base + 'warehouses?status=invalid').status_code, 400)

    def test_csrf_required_and_delete_not_available(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        session = client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()
        self.assertEqual(client.post(self.base + 'products/add', self.product_data()).status_code, 403)
        self.assertEqual(self.client.delete(self.base + 'products/' + str(self.product.pk)).status_code, 405)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_audit_failure_rolls_back_record(self):
        with patch('catalog.views.CatalogEvent.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.base + 'warehouses/add', {'name': 'Rollback'})
        self.assertFalse(Warehouse.objects.filter(name='Rollback').exists())
