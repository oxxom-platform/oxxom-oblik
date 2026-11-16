import json
import time
from django.test import TestCase
from accounts.models import User, Organization, Membership
from catalog.models import Unit, Product


class ProductCreationFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('create-goods@example.com', 'Only-For-Test-Password-782!')
        self.org = Organization.objects.create(name='Empty catalog')
        self.member = Membership.objects.create(user=self.user, organization=self.org, role='owner')
        self.client.force_login(self.user)
        session = self.client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()
        self.base = f'/api/v1/organizations/{self.org.pk}'

    def test_empty_organization_unit_product_warehouse_and_saved_opening(self):
        self.assertEqual(self.client.get(self.base+'/catalog/products?product_kind=goods').json()['total'], 0)
        unit = self.client.post(self.base+'/catalog/units/add', {'name':'Штука','symbol':'шт'}).json()['item']
        product_response = self.client.post(self.base+'/catalog/products/add', {'name':'Перший товар','kind':'goods','unit':unit['id'],'price':'100.00'})
        self.assertEqual(product_response.status_code, 201)
        product = product_response.json()['item']
        warehouse = self.client.post(self.base+'/catalog/warehouses/add', {'name':'Основний'}).json()['item']
        payload = {'kind':'opening','date':'2026-11-15','warehouse':warehouse['id'],'note':'Keep note','lines':[{'product':product['id'],'quantity':'10','price':'60.00'}]}
        response = self.client.post(self.base+'/inventory/add', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['document']['total'], '600.00')
        self.assertEqual(response.json()['document']['lines'][0]['name'], 'Перший товар')
        self.assertEqual(Product.objects.get().price, 100)

    def test_services_and_archived_goods_are_distinguished_by_filter(self):
        unit = Unit.objects.create(organization=self.org, name='Unit', symbol='u')
        Product.objects.create(organization=self.org,name='Service',kind='service',unit=unit,price=1)
        Product.objects.create(organization=self.org,name='Old goods',kind='goods',unit=unit,price=1,is_active=False)
        self.assertEqual(self.client.get(self.base+'/catalog/products').json()['total'], 1)
        self.assertEqual(self.client.get(self.base+'/catalog/products?product_kind=goods').json()['total'], 0)
        self.assertEqual(self.client.get(self.base+'/catalog/products?product_kind=goods&status=archived').json()['total'], 1)
        self.assertEqual(self.client.get(self.base+'/catalog/products?product_kind=invalid').status_code, 400)

    def test_storekeeper_cannot_create_goods_or_units(self):
        Membership.objects.filter(pk=self.member.pk).update(role='storekeeper')
        self.assertEqual(self.client.post(self.base+'/catalog/units/add',{'name':'No','symbol':'n'}).status_code, 403)
        self.assertEqual(self.client.post(self.base+'/catalog/products/add',{'name':'No'}).status_code, 403)

    def test_other_organization_unit_is_not_accepted(self):
        other = Organization.objects.create(name='Other')
        unit = Unit.objects.create(organization=other,name='Foreign',symbol='f')
        result = self.client.post(self.base+'/catalog/products/add',{'name':'Wrong','kind':'goods','unit':str(unit.pk),'price':'0'})
        self.assertEqual(result.status_code, 400)
        self.assertFalse(Product.objects.exists())
