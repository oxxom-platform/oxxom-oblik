import json
import time
from django.test import TestCase
from accounts.models import User, Organization, Membership
from catalog.models import Unit, Product


class EmptyCounterpartyFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('empty-sales@example.com', 'Only-For-Tests-Password-782!')
        self.organization = Organization.objects.create(name='Empty customer list')
        Membership.objects.create(user=self.user, organization=self.organization, role='owner')
        unit = Unit.objects.create(organization=self.organization, name='Послуга', symbol='посл')
        self.product = Product.objects.create(organization=self.organization, name='Service', kind='service', unit=unit, price='1500')
        self.client.force_login(self.user)
        session = self.client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()
        self.base = f'/api/v1/organizations/{self.organization.pk}'

    def test_create_customer_select_it_and_save_service_sale(self):
        self.assertEqual(self.client.get(self.base + '/catalog/counterparties').json()['total'], 0)
        created = self.client.post(self.base + '/catalog/counterparties/add', {'name': 'Новий клієнт', 'kind': 'person'})
        self.assertEqual(created.status_code, 201)
        customer = created.json()['item']
        choices = self.client.get(self.base + '/catalog/counterparties').json()['items']
        self.assertEqual(choices[0]['id'], customer['id'])
        payload = {'date': '2026-11-15', 'counterparty': customer['id'], 'lines': [{'product': str(self.product.pk), 'quantity': '2', 'price': '1500.00'}]}
        saved = self.client.post(self.base + '/documents/add', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(saved.status_code, 201, saved.content)
        self.assertEqual(saved.json()['document']['counterparty_snapshot']['name'], 'Новий клієнт')
        self.assertEqual(saved.json()['document']['total'], '3000.00')

    def test_manager_can_create_customer_but_cannot_create_warehouse(self):
        Membership.objects.filter(user=self.user).update(role='manager')
        self.assertEqual(self.client.post(self.base + '/catalog/counterparties/add', {'name': 'Client', 'kind': 'company'}).status_code, 201)
        self.assertEqual(self.client.post(self.base + '/catalog/warehouses/add', {'name': 'Warehouse'}).status_code, 403)

    def test_customer_creation_does_not_grant_access_to_other_organization(self):
        other = Organization.objects.create(name='Other')
        self.assertEqual(self.client.post(f'/api/v1/organizations/{other.pk}/catalog/counterparties/add', {'name': 'Unauthorized', 'kind': 'person'}).status_code, 403)
