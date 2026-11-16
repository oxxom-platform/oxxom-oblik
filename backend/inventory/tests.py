import json
import time
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, Client
from accounts.models import User, Organization, Membership
from catalog.models import Unit, Product, Counterparty, Warehouse
from .models import StockDraft, StockLine, StockSequence, StockEvent


class StockDraftTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('stock@example.com', 'Only-For-Test-Password-782!')
        cls.org = Organization.objects.create(name='A')
        cls.other = Organization.objects.create(name='B')
        cls.member = Membership.objects.create(user=cls.user, organization=cls.org, role='owner')
        unit = Unit.objects.create(organization=cls.org, name='Штука', symbol='шт')
        cls.product = Product.objects.create(organization=cls.org, name='Товар', kind='goods', unit=unit, price='100')
        cls.service = Product.objects.create(organization=cls.org, name='Послуга', kind='service', unit=unit, price='100')
        cls.warehouse = Warehouse.objects.create(organization=cls.org, name='Склад')
        cls.supplier = Counterparty.objects.create(organization=cls.org, name='Постачальник', kind='company', bank_details='private')
        cls.foreign_warehouse = Warehouse.objects.create(organization=cls.other, name='Foreign')
        other_unit = Unit.objects.create(organization=cls.other, name='Foreign', symbol='f')
        cls.foreign_product = Product.objects.create(organization=cls.other, name='Foreign', kind='goods', unit=other_unit, price='1')

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()
        self.base = f'/api/v1/organizations/{self.org.pk}/inventory'

    def payload(self, **extra):
        return {'kind': 'opening', 'date': '2026-11-15', 'warehouse': str(self.warehouse.pk), 'lines': [{'product': str(self.product.pk), 'quantity': '10', 'price': '60.00'}], **extra}

    def post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type='application/json')

    def create(self, **extra):
        response = self.post(self.base + '/add', self.payload(**extra))
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()['document']

    def editing(self, doc, **extra):
        data = self.payload(kind=doc['kind'], version=doc['version'], counterparty=doc.get('counterparty'), lines=[{'id': row['id'], 'product': row['product'], 'quantity': row['quantity'], **({'price': row['price']} if 'price' in row else {})} for row in doc['lines']])
        data.update(extra)
        return data

    def test_opening_and_receipt_with_separate_numbers(self):
        opening = self.create()
        receipt = self.create(kind='receipt', counterparty=str(self.supplier.pk))
        self.assertEqual(opening['number'], 'ПЗ-2026-000001')
        self.assertEqual(receipt['number'], 'НД-2026-000001')
        self.assertEqual(opening['total'], '600.00')
        self.assertEqual(opening['lines'][0]['price'], '60.00')
        self.assertEqual(receipt['counterparty_snapshot']['name'], 'Постачальник')
        self.assertEqual(StockEvent.objects.count(), 2)

    def test_price_is_optional_and_not_taken_from_sale_price(self):
        doc = self.create(lines=[{'product': str(self.product.pk), 'quantity': '10'}])
        self.assertIsNone(doc['total'])
        self.assertEqual(doc['lines'][0]['price'], '')
        doc = self.create(lines=[{'product': str(self.product.pk), 'quantity': '0.5', 'price': '0.01'}])
        self.assertEqual(doc['total'], '0.01')

    def test_services_foreign_products_and_foreign_warehouses_rejected(self):
        for extra in [{'lines': [{'product': str(self.service.pk), 'quantity': '1'}]}, {'lines': [{'product': str(self.foreign_product.pk), 'quantity': '1'}]}, {'warehouse': str(self.foreign_warehouse.pk)}, {'warehouse': ''}]:
            self.assertEqual(self.post(self.base + '/add', self.payload(**extra)).status_code, 400)
        self.assertFalse(StockDraft.objects.exists())
        self.assertFalse(StockSequence.objects.exists())

    def test_supplier_is_optional_for_receipt_but_forbidden_for_opening(self):
        self.assertEqual(self.post(self.base + '/add', self.payload(counterparty=str(self.supplier.pk))).status_code, 400)
        self.create(kind='receipt')

    def test_validation_line_limits_and_price_precision(self):
        for lines in [[], [{}]*101, [{'product': str(self.product.pk), 'quantity': '0'}], [{'product': str(self.product.pk), 'quantity': '1.0001'}], [{'product': str(self.product.pk), 'quantity': '1', 'price': '-1'}], [{'product': str(self.product.pk), 'quantity': '1', 'price': 'NaN'}], [{'product': str(self.product.pk), 'quantity': '1', 'price': '0.001'}]]:
            self.assertEqual(self.post(self.base + '/add', self.payload(lines=lines)).status_code, 400)

    def test_snapshots_and_stale_version(self):
        doc = self.create(kind='receipt', counterparty=str(self.supplier.pk))
        Product.objects.filter(pk=self.product.pk).update(name='Changed')
        Warehouse.objects.filter(pk=self.warehouse.pk).update(name='Changed')
        Counterparty.objects.filter(pk=self.supplier.pk).update(name='Changed')
        url = self.base + '/' + doc['id'] + '/save'
        response = self.post(url, self.editing(doc, note='Updated'))
        self.assertEqual(response.status_code, 200)
        saved = response.json()['document']
        self.assertEqual(saved['lines'][0]['name'], 'Товар')
        self.assertEqual(saved['warehouse_name'], 'Склад')
        self.assertEqual(saved['counterparty_snapshot']['name'], 'Постачальник')
        self.assertEqual(self.post(url, self.editing(doc)).status_code, 409)

    def test_storekeeper_can_edit_quantity_but_never_read_or_set_costs(self):
        doc = self.create(kind='receipt', counterparty=str(self.supplier.pk))
        Membership.objects.filter(pk=self.member.pk).update(role='storekeeper')
        response = self.client.get(self.base + '/' + doc['id'])
        safe = response.json()['document']
        self.assertNotIn('total', safe)
        self.assertNotIn('counterparty_snapshot', safe)
        self.assertNotIn('counterparty', safe)
        self.assertNotIn('price', safe['lines'][0])
        self.assertNotIn('amount', safe['lines'][0])
        update = self.editing(safe)
        update['lines'][0]['quantity'] = '20'
        self.assertEqual(self.post(self.base + '/' + doc['id'] + '/save', update).status_code, 200)
        self.assertEqual(StockLine.objects.get().price, Decimal('60.00'))
        self.assertEqual(StockDraft.objects.get().total, Decimal('1200.00'))
        self.assertEqual(StockDraft.objects.get().counterparty_id, self.supplier.pk)
        self.assertEqual(self.post(self.base + '/add', self.payload()).status_code, 403)
        safe_new = self.payload(lines=[{'product': str(self.product.pk), 'quantity': '1'}])
        self.assertEqual(self.post(self.base + '/add', safe_new).status_code, 201)
        self.assertTrue(all('total' not in item for item in self.client.get(self.base).json()['items']))

    def test_reader_roles_do_not_mutate_and_costs_are_only_owner_accountant(self):
        for role in ['manager', 'viewer', 'accountant']:
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            self.assertEqual(self.client.get(self.base).status_code, 200)
            self.assertEqual(self.post(self.base + '/add', self.payload()).status_code, 201 if role=='accountant' else 403)

    def test_cross_org_access_and_revocation(self):
        doc = self.create()
        foreign_base = f'/api/v1/organizations/{self.other.pk}/inventory'
        self.assertEqual(self.client.get(foreign_base).status_code, 403)
        Membership.objects.create(user=self.user, organization=self.other, role='owner')
        self.assertEqual(self.client.get(foreign_base + '/' + doc['id']).status_code, 404)
        self.assertEqual(self.post(foreign_base + '/' + doc['id'] + '/save', self.editing(doc)).status_code, 404)
        Membership.objects.filter(pk=self.member.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.base).status_code, 403)

    def test_archive_restore_and_post_requires_idempotency(self):
        doc = self.create()
        url = self.base + '/' + doc['id']
        self.assertEqual(self.post(url + '/state', {'version': 1, 'is_active': False}).status_code, 200)
        self.assertEqual(self.client.get(self.base).json()['total'], 0)
        self.assertEqual(self.post(url + '/save', self.editing(doc, version=2)).status_code, 409)
        self.assertEqual(self.post(url + '/state', {'version': 2, 'is_active': True}).status_code, 200)
        self.assertEqual(self.post(url + '/post', {'version': 3}).status_code, 400)
        self.assertEqual(StockDraft.objects.get().status, 'draft')
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_audit_failure_rolls_back_everything(self):
        with patch('inventory.services.StockEvent.objects.create', side_effect=RuntimeError('Audit failed')):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertFalse(StockDraft.objects.exists())
        self.assertFalse(StockSequence.objects.exists())
        self.assertFalse(StockLine.objects.exists())

    def test_mfa_csrf_and_page(self):
        client = Client()
        client.force_login(self.user)
        self.assertEqual(client.get(self.base).status_code, 401)
        self.assertEqual(client.get('/inventory/').status_code, 302)
        self.assertEqual(self.client.get('/inventory/').status_code, 200)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        session = csrf_client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()
        self.assertEqual(csrf_client.post(self.base + '/add', data=json.dumps(self.payload()), content_type='application/json').status_code, 403)

    def test_number_changes_with_year_and_kind(self):
        doc = self.create()
        result = self.post(self.base + '/' + doc['id'] + '/save', self.editing(doc, kind='receipt', date='2025-12-31'))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['document']['number'], 'НД-2025-000001')
