import json
import time
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, Client
from accounts.models import User, Organization, Membership
from catalog.models import Unit, Product, Counterparty, Warehouse
from .models import Document, DocumentLine, DocumentSequence, DocumentEvent


class DraftSalesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('sales@example.com', 'Tests-Only-Password-782!')
        cls.org = Organization.objects.create(name='A')
        cls.other = Organization.objects.create(name='B')
        cls.member = Membership.objects.create(user=cls.user, organization=cls.org, role='owner')
        cls.unit = Unit.objects.create(organization=cls.org, name='Година', symbol='год')
        cls.service = Product.objects.create(organization=cls.org, name='Консультація', kind='service', unit=cls.unit, price='1500.00')
        cls.goods = Product.objects.create(organization=cls.org, name='Товар', kind='goods', unit=cls.unit, price='100.00')
        cls.customer = Counterparty.objects.create(organization=cls.org, name='Клієнт', kind='company', code='12345678', bank_details='Original')
        cls.warehouse = Warehouse.objects.create(organization=cls.org, name='Склад')
        cls.foreign_customer = Counterparty.objects.create(organization=cls.other, name='Foreign', kind='person')
        cls.foreign_warehouse = Warehouse.objects.create(organization=cls.other, name='Foreign')
        cls.foreign_unit = Unit.objects.create(organization=cls.other, name='Foreign', symbol='f')
        cls.foreign_product = Product.objects.create(organization=cls.other, name='Foreign', kind='goods', unit=cls.foreign_unit, price='900')

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()
        self.base = f'/api/v1/organizations/{self.org.pk}/documents'

    def payload(self, **extra):
        return {'date': '2026-11-15', 'counterparty': str(self.customer.pk), 'warehouse': str(self.warehouse.pk), 'note': 'Test', 'lines': [
            {'product': str(self.service.pk), 'quantity': '2', 'price': '1500.00'},
            {'product': str(self.goods.pk), 'quantity': '3', 'price': '100.00'}], **extra}

    def post(self, url, data):
        return self.client.post(url, data=json.dumps(data), content_type='application/json')

    def create(self, **extra):
        response = self.post(self.base + '/add', self.payload(**extra))
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()['document']

    def editing(self, doc, **extra):
        data = self.payload(version=doc['version'], lines=[{'id': row['id'], 'product': row['product'], 'quantity': row['quantity'], 'price': row['price']} for row in doc['lines']])
        data.update(extra)
        return data

    def test_doc01_total_and_persistence_after_new_session(self):
        doc = self.create(total='0', status='posted', organization=str(self.other.pk))
        self.assertEqual(doc['total'], '3300.00')
        self.assertEqual(doc['status'], 'draft')
        self.assertEqual(doc['number'], 'РЛ-2026-000001')
        self.assertEqual(len(doc['lines']), 2)
        self.assertEqual(Document.objects.get().organization, self.org)
        self.client.logout()
        self.setUp()
        response = self.client.get(self.base + '/' + doc['id'])
        self.assertEqual(response.json()['document']['total'], '3300.00')
        self.assertEqual(len(response.json()['document']['lines']), 2)
        self.assertEqual(DocumentEvent.objects.get().action, 'create')

    def test_no_manual_positions_or_client_snapshots(self):
        self.assertEqual(self.post(self.base + '/add', self.payload(lines=[{'name': 'Manual', 'quantity': '1', 'price': '1'}])).status_code, 400)
        doc = self.create(lines=[{'product': str(self.service.pk), 'name': 'Injected', 'unit_symbol': 'fake', 'quantity': '1', 'price': '10', 'amount': '1000'}])
        self.assertEqual(doc['lines'][0]['name'], 'Консультація')
        self.assertEqual(doc['lines'][0]['unit_symbol'], 'год')
        self.assertEqual(doc['total'], '10.00')

    def test_half_up_and_total_of_rounded_lines(self):
        doc = self.create(lines=[{'product': str(self.service.pk), 'quantity': '0.5', 'price': '0.01'}]*2, warehouse=None)
        self.assertEqual([line['amount'] for line in doc['lines']], ['0.01', '0.01'])
        self.assertEqual(doc['total'], '0.02')

    def test_snapshots_survive_catalog_change_and_note_edit(self):
        doc = self.create()
        Product.objects.filter(pk=self.service.pk).update(name='Renamed', kind='goods', price='99')
        Unit.objects.filter(pk=self.unit.pk).update(name='New unit', symbol='new')
        Counterparty.objects.filter(pk=self.customer.pk).update(name='New customer', bank_details='New bank')
        Warehouse.objects.filter(pk=self.warehouse.pk).update(name='New warehouse')
        response = self.post(self.base + '/' + doc['id'] + '/save', self.editing(doc, note='Changed'))
        self.assertEqual(response.status_code, 200, response.content)
        saved = response.json()['document']
        self.assertEqual(saved['lines'][0]['name'], 'Консультація')
        self.assertEqual(saved['lines'][0]['kind'], 'service')
        self.assertEqual(saved['lines'][0]['unit_symbol'], 'год')
        self.assertEqual(saved['counterparty_snapshot']['name'], 'Клієнт')
        self.assertEqual(saved['counterparty_snapshot']['bank_details'], 'Original')
        self.assertEqual(saved['warehouse_name'], 'Склад')
        self.assertEqual(saved['total'], '3300.00')

    def test_line_reorder_remove_add_and_price_change(self):
        doc = self.create()
        first, second = doc['lines']
        rows = [{'id': second['id'], 'product': second['product'], 'quantity': '1', 'price': '50'}, {'id': first['id'], 'product': first['product'], 'quantity': '1', 'price': '100'}]
        response = self.post(self.base + '/' + doc['id'] + '/save', self.payload(version=1, lines=rows))
        self.assertEqual(response.status_code, 200)
        saved = response.json()['document']
        self.assertEqual([line['id'] for line in saved['lines']], [second['id'], first['id']])
        self.assertEqual(saved['total'], '150.00')
        response = self.post(self.base + '/' + doc['id'] + '/save', self.payload(version=2, lines=[rows[0]]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DocumentLine.objects.count(), 1)

    def test_foreign_counterparty_warehouse_and_product_denied(self):
        variants = [{'counterparty': str(self.foreign_customer.pk)}, {'warehouse': str(self.foreign_warehouse.pk)}, {'lines': [{'product': str(self.foreign_product.pk), 'quantity': '1', 'price': '1'}]}]
        for extra in variants:
            response = self.post(self.base + '/add', self.payload(**extra))
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Document.objects.exists())
        self.assertFalse(DocumentSequence.objects.exists())

    def test_known_foreign_document_uuid_not_readable_or_writable(self):
        doc = self.create()
        foreign_base = f'/api/v1/organizations/{self.other.pk}/documents'
        self.assertEqual(self.client.get(foreign_base).status_code, 403)
        self.assertEqual(self.client.get(foreign_base + '/' + doc['id']).status_code, 403)
        Membership.objects.create(user=self.user, organization=self.other, role='owner')
        self.assertEqual(self.client.get(foreign_base + '/' + doc['id']).status_code, 404)
        for suffix in ['save', 'state', 'copy']:
            self.assertEqual(self.post(foreign_base + '/' + doc['id'] + '/' + suffix, {'version': 1}).status_code, 404)

    def test_line_count_and_precision_and_types_are_validated(self):
        variants = [[], [{}]*101, [{'product': str(self.service.pk), 'quantity': '0', 'price': '1'}], [{'product': str(self.service.pk), 'quantity': '1.0001', 'price': '1'}], [{'product': str(self.service.pk), 'quantity': '1', 'price': '-1'}], [{'product': str(self.service.pk), 'quantity': '1', 'price': '1.001'}], [{'product': str(self.service.pk), 'quantity': 'NaN', 'price': '1'}], [{'product': str(self.service.pk), 'quantity': '1', 'price': 'Infinity'}], [{'product': str(self.service.pk), 'quantity': 1.0, 'price': '1'}], [None]]
        for rows in variants:
            with self.subTest(rows=rows[:1]):
                self.assertEqual(self.post(self.base + '/add', self.payload(lines=rows)).status_code, 400)
        self.assertFalse(Document.objects.exists())

    def test_goods_require_warehouse_services_do_not(self):
        self.assertEqual(self.post(self.base + '/add', self.payload(warehouse=None)).status_code, 400)
        self.create(warehouse=None, lines=[{'product': str(self.service.pk), 'quantity': '1', 'price': '1500'}])

    def test_version_conflict_does_not_overwrite(self):
        doc = self.create()
        url = self.base + '/' + doc['id'] + '/save'
        self.assertEqual(self.post(url, self.editing(doc, note='First')).status_code, 200)
        self.assertEqual(self.post(url, self.editing(doc, note='Lost')).status_code, 409)
        self.assertEqual(Document.objects.get().note, 'First')
        self.assertEqual(DocumentEvent.objects.count(), 2)

    def test_invalid_row_id_and_duplicate_row_id_denied(self):
        doc = self.create()
        foreign_line_doc = self.create()
        data = self.editing(doc)
        data['lines'][0]['id'] = foreign_line_doc['lines'][0]['id']
        self.assertEqual(self.post(self.base + '/' + doc['id'] + '/save', data).status_code, 400)
        data = self.editing(doc)
        data['lines'] = [data['lines'][0]]*2
        self.assertEqual(self.post(self.base + '/' + doc['id'] + '/save', data).status_code, 400)

    def test_archive_restore_and_copy(self):
        doc = self.create()
        url = self.base + '/' + doc['id']
        self.assertEqual(self.post(url + '/state', {'version': 1, 'is_active': False}).status_code, 200)
        self.assertEqual(self.client.get(self.base).json()['total'], 0)
        self.assertEqual(self.client.get(self.base + '?status=archived').json()['total'], 1)
        self.assertEqual(self.post(url + '/save', self.editing(doc, version=2)).status_code, 409)
        response = self.post(url + '/copy', {'version': 2})
        self.assertEqual(response.status_code, 201)
        copied = response.json()['document']
        self.assertNotEqual(copied['id'], doc['id'])
        self.assertEqual(copied['total'], '3300.00')
        self.assertTrue(copied['is_active'])
        self.assertEqual(self.post(url + '/state', {'version': 2, 'is_active': True}).status_code, 200)
        self.assertEqual(self.client.get(self.base).json()['total'], 2)

    def test_archived_references_retained_but_not_selectable_for_new_or_copy(self):
        doc = self.create()
        Product.objects.filter(pk=self.service.pk).update(is_active=False)
        Counterparty.objects.filter(pk=self.customer.pk).update(is_active=False)
        Warehouse.objects.filter(pk=self.warehouse.pk).update(is_active=False)
        self.assertEqual(self.post(self.base + '/' + doc['id'] + '/save', self.editing(doc)).status_code, 200)
        self.assertEqual(self.post(self.base + '/add', self.payload()).status_code, 400)
        self.assertEqual(self.post(self.base + '/' + doc['id'] + '/copy', {'version': 2}).status_code, 400)

    def test_numbers_scoped_by_org_and_year_and_date_change(self):
        first = self.create()
        second = self.create()
        self.assertEqual(second['number'], 'РЛ-2026-000002')
        old = self.create(date='2025-12-31')
        self.assertEqual(old['number'], 'РЛ-2025-000001')
        response = self.post(self.base + '/' + old['id'] + '/save', self.editing(old, date='2026-01-01'))
        self.assertEqual(response.json()['document']['number'], 'РЛ-2026-000003')
        Membership.objects.create(user=self.user, organization=self.other, role='owner')
        response = self.post(f'/api/v1/organizations/{self.other.pk}/documents/add', self.payload(counterparty=str(self.foreign_customer.pk), warehouse=str(self.foreign_warehouse.pk), lines=[{'product': str(self.foreign_product.pk), 'quantity': '1', 'price': '1'}]))
        self.assertEqual(response.json()['document']['number'], first['number'])

    def test_roles_and_revocation(self):
        doc = self.create()
        for role in ['accountant', 'manager', 'viewer', 'storekeeper']:
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            self.assertEqual(self.client.get(self.base).status_code, 403 if role == 'storekeeper' else 200)
            self.assertEqual(self.post(self.base + '/add', self.payload()).status_code, 201 if role in ['accountant', 'manager'] else 403)
        Membership.objects.filter(pk=self.member.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.base + '/' + doc['id']).status_code, 403)

    def test_audit_failure_rolls_back_number_header_and_lines(self):
        with patch('documents.services.DocumentEvent.objects.create', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertFalse(Document.objects.exists())
        self.assertFalse(DocumentLine.objects.exists())
        self.assertFalse(DocumentSequence.objects.exists())

    def test_mfa_csrf_and_delete_prohibition(self):
        doc = self.create()
        client = Client()
        client.force_login(self.user)
        self.assertEqual(client.get(self.base).status_code, 401)
        self.assertEqual(client.get('/documents/').status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        session = csrf_client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()
        self.assertEqual(csrf_client.post(self.base + '/add', data=json.dumps(self.payload()), content_type='application/json').status_code, 403)
        self.assertEqual(self.client.delete(self.base + '/' + doc['id']).status_code, 405)
        self.assertEqual(self.client.get('/documents/').status_code, 200)

    def test_malformed_request_and_invalid_date(self):
        self.assertEqual(self.client.post(self.base + '/add', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(self.post(self.base + '/add', []).status_code, 400)
        self.assertEqual(self.post(self.base + '/add', self.payload(date='invalid')).status_code, 400)

    def test_search_and_archive_filter_validation(self):
        self.create(note='Searchable')
        self.assertEqual(self.client.get(self.base + '?q=Searchable').json()['total'], 1)
        self.assertEqual(self.client.get(self.base + '?q=absent').json()['total'], 0)
        self.assertEqual(self.client.get(self.base + '?page=2').json()['items'], [])
        self.assertEqual(self.client.get(self.base + '?status=bad').status_code, 400)


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature


class ConcurrentDraftTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user('concurrent-sales@example.com', 'Only-For-Test-Password-782!')
        self.org = Organization.objects.create(name='Concurrent')
        Membership.objects.create(user=self.user, organization=self.org, role='owner')
        unit = Unit.objects.create(organization=self.org, name='Hour', symbol='h')
        product = Product.objects.create(organization=self.org, name='Service', kind='service', unit=unit, price='100')
        customer = Counterparty.objects.create(organization=self.org, name='Client', kind='person')
        self.base = f'/api/v1/organizations/{self.org.pk}/documents'
        self.data = {'date': '2026-11-15', 'counterparty': str(customer.pk), 'lines': [{'product': str(product.pk), 'quantity': '1', 'price': '100'}]}
        self.clients = [Client(), Client()]
        for client in self.clients:
            client.force_login(self.user)
            session = client.session
            session['mfa_started'] = session['mfa_last'] = time.time()
            session.save()

    def race(self, url, payloads):
        barrier = Barrier(2)
        def invoke(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = self.clients[index].post(url, data=json.dumps(payloads[index]), content_type='application/json')
                return response.status_code, response.json()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(invoke, range(2)))

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_creates_get_distinct_numbers(self):
        results = self.race(self.base + '/add', [self.data, self.data])
        self.assertEqual([r[0] for r in results], [201, 201])
        self.assertEqual(len({r[1]['document']['number'] for r in results}), 2)
        self.assertEqual(Document.objects.count(), 2)

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_save_only_one_version_succeeds(self):
        response = self.clients[0].post(self.base + '/add', data=json.dumps(self.data), content_type='application/json')
        document = response.json()['document']
        results = self.race(self.base + '/' + document['id'] + '/save', [{**self.data, 'version': 1, 'note': 'A'}, {**self.data, 'version': 1, 'note': 'B'}])
        self.assertEqual(sorted(r[0] for r in results), [200, 409])
        self.assertEqual(Document.objects.get().version, 2)
        self.assertEqual(DocumentEvent.objects.count(), 2)
