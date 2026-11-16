import json
import time
from datetime import date
from decimal import Decimal
from uuid import uuid4, UUID
from unittest.mock import patch
from django.test import TestCase, Client
from accounts.models import User, Organization, Membership
from catalog.models import Unit, Product, Counterparty, Warehouse
from documents.models import Document
from inventory.models import StockDraft
from .models import Balance, Posting, Movement, RequestRecord, Period, PeriodEvent


class PostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user=User.objects.create_user('posting@example.com','Test-Only-Password-782!')
        cls.org=Organization.objects.create(name='A')
        cls.other=Organization.objects.create(name='B')
        cls.member=Membership.objects.create(user=cls.user,organization=cls.org,role='owner')
        cls.unit=Unit.objects.create(organization=cls.org,name='Штука',symbol='шт')
        cls.goods=Product.objects.create(organization=cls.org,name='Товар',kind='goods',unit=cls.unit,price='100')
        cls.service=Product.objects.create(organization=cls.org,name='Послуга',kind='service',unit=cls.unit,price='1500')
        cls.warehouse=Warehouse.objects.create(organization=cls.org,name='Склад')
        cls.customer=Counterparty.objects.create(organization=cls.org,name='Client',kind='company')

    def setUp(self):
        self.today=patch('posting.services.timezone.localdate',return_value=date(2026,11,15))
        self.today.start();self.addCleanup(self.today.stop)
        self.client.force_login(self.user)
        session=self.client.session;session['mfa_started']=session['mfa_last']=time.time();session.save()
        self.base=f'/api/v1/organizations/{self.org.pk}'

    def post(self,url,data):
        return self.client.post(url,data=json.dumps(data),content_type='application/json')

    def stock(self,quantity='10',price='60.00',kind='opening',day='2026-11-15'):
        payload={'kind':kind,'date':day,'warehouse':str(self.warehouse.pk),'counterparty':str(self.customer.pk) if kind=='receipt' else None,'lines':[{'product':str(self.goods.pk),'quantity':quantity,'price':price}]}
        response=self.post(self.base+'/inventory/add',payload)
        self.assertEqual(response.status_code,201,response.content)
        return response.json()['document']

    def sale(self,quantity='3',service=False,day='2026-11-15',lines=None):
        payload={'date':day,'warehouse':None if service else str(self.warehouse.pk),'counterparty':str(self.customer.pk),'lines':lines or [{'product':str(self.service.pk if service else self.goods.pk),'quantity':quantity,'price':'100.00'}]}
        response=self.post(self.base+'/documents/add',payload)
        self.assertEqual(response.status_code,201,response.content)
        return response.json()['document']

    def execute(self,doc,source='inventory',action='post',key=None,version=None):
        return self.post(self.base+f'/{source}/{doc["id"]}/{action}',{'version':doc['version'] if version is None else version,'idempotency_key':str(key or uuid4())})

    def posted(self,doc,source='inventory'):
        response=self.execute(doc,source)
        self.assertEqual(response.status_code,200,response.content)
        return response.json()['document']

    def test_opening_sale_and_latest_sale_reversal(self):
        opening=self.posted(self.stock())
        sale=self.sale()
        response=self.execute(sale,'documents')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['posting']['cost_total'],'180.00')
        balance=Balance.objects.get()
        self.assertEqual(balance.quantity,Decimal('7'))
        self.assertEqual(balance.value,Decimal('420.00'))
        saved=response.json()['document']
        reversal=self.execute(saved,'documents','reverse')
        self.assertEqual(reversal.status_code,200,reversal.content)
        balance.refresh_from_db()
        self.assertEqual(balance.quantity,Decimal('10'))
        self.assertEqual(balance.value,Decimal('600.00'))
        self.assertEqual(Document.objects.get().status,'reversed')
        self.assertEqual(Movement.objects.filter(is_reversal=True).count(),1)

    def test_moving_average_after_receipt(self):
        self.posted(self.stock())
        self.posted(self.stock(price='80.00',kind='receipt'))
        response=self.execute(self.sale(),'documents')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['posting']['cost_total'],'210.00')
        self.assertEqual(Balance.objects.get().value,Decimal('1190.00'))
        balances=self.client.get(self.base+'/balances').json()
        self.assertEqual(balances['items'][0]['average_cost'],'70.000000')

    def test_idempotency_repeat_same_key_and_changed_body(self):
        doc=self.stock();key=uuid4()
        first=self.execute(doc,key=key)
        second=self.execute(doc,key=key)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(Movement.objects.count(),1)
        self.assertEqual(self.execute(doc,key=key,version=2).status_code,409)
        self.assertEqual(self.execute(doc).status_code,409)
        self.assertEqual(RequestRecord.objects.count(),1)

    def test_idempotency_key_is_bound_to_document_and_operation(self):
        first=self.stock();second=self.stock(kind='receipt');key=uuid4()
        self.assertEqual(self.execute(first,key=key).status_code,200)
        self.assertEqual(self.execute(second,key=key).status_code,409)
        self.assertEqual(self.execute(first,action='reverse',key=key,version=2).status_code,409)

    def test_insufficient_stock_rolls_back_whole_operation(self):
        self.posted(self.stock(quantity='2'))
        sale=self.sale(quantity='3')
        before=Movement.objects.count()
        self.assertEqual(self.execute(sale,'documents').status_code,409)
        self.assertEqual(Movement.objects.count(),before)
        self.assertEqual(Posting.objects.count(),1)
        self.assertEqual(Document.objects.get().status,'draft')
        self.assertEqual(Balance.objects.get().quantity,Decimal('2'))
        extra=Product.objects.create(id=UUID(int=(1<<128)-1),organization=self.org,name='Missing second product',kind='goods',unit=self.unit,price='100')
        mixed=self.sale(lines=[{'product':str(self.goods.pk),'quantity':'1','price':'100'},{'product':str(extra.pk),'quantity':'1','price':'100'}])
        self.assertEqual(self.execute(mixed,'documents').status_code,409)
        self.assertEqual(Balance.objects.get().quantity,Decimal('2'))
        self.assertEqual(Balance.objects.get().value,Decimal('120.00'))
        self.assertEqual(Movement.objects.count(),before)
        self.assertEqual(RequestRecord.objects.count(),1)

    def test_duplicate_product_rows_are_aggregated_before_availability_check(self):
        self.posted(self.stock(quantity='2'))
        sale=self.sale(lines=[{'product':str(self.goods.pk),'quantity':'2','price':'100'}]*2)
        self.assertEqual(self.execute(sale,'documents').status_code,409)
        self.assertEqual(Balance.objects.get().quantity,Decimal('2'))

    def test_last_quantity_consumes_all_residual_value(self):
        self.posted(self.stock(quantity='0.003',price='1.67'))
        sale=self.sale(lines=[{'product':str(self.goods.pk),'quantity':'0.001','price':'100'}]*3)
        response=self.execute(sale,'documents')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['posting']['cost_total'],'0.01')
        balance=Balance.objects.get()
        self.assertEqual(balance.quantity,0)
        self.assertEqual(balance.value,0)
        values=list(Movement.objects.filter(posting__sale_id=sale['id']).values_list('value',flat=True))
        self.assertTrue(all(value<=0 for value in values))
        self.assertEqual(sum(values),Decimal('-0.01'))

    def test_service_posting_has_no_stock_movement(self):
        response=self.execute(self.sale(service=True),'documents')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['posting']['cost_total'],'0.00')
        self.assertFalse(Movement.objects.exists())
        self.assertFalse(Balance.objects.exists())

    def test_missing_cost_or_supplier_prevents_stock_posting(self):
        self.assertEqual(self.execute(self.stock(price='')).status_code,400)
        doc=self.stock(kind='receipt')
        StockDraft.objects.filter(pk=doc['id']).update(counterparty=None)
        self.assertEqual(self.execute(doc).status_code,400)
        self.assertFalse(Posting.objects.exists())
        self.assertFalse(Balance.objects.exists())

    def test_posted_documents_cannot_edit_archive_or_post_twice(self):
        doc=self.posted(self.stock())
        self.assertEqual(self.post(self.base+'/inventory/'+doc['id']+'/save',{'version':doc['version']}).status_code,409)
        self.assertEqual(self.post(self.base+'/inventory/'+doc['id']+'/state',{'version':doc['version'],'is_active':False}).status_code,400)
        self.assertEqual(self.execute(doc).status_code,409)
        sale=self.posted(self.sale(),'documents')
        self.assertEqual(self.post(self.base+'/documents/'+sale['id']+'/save',{'version':sale['version']}).status_code,409)

    def test_opening_cannot_follow_an_active_movement(self):
        self.posted(self.stock())
        self.assertEqual(self.execute(self.stock()).status_code,409)

    def test_backdated_and_future_postings_are_rejected(self):
        self.posted(self.stock(day='2026-11-14'))
        self.assertEqual(self.execute(self.sale(day='2026-11-13'),'documents').status_code,409)
        self.assertEqual(self.execute(self.stock(kind='receipt',day='2026-11-16')).status_code,400)

    def test_reversal_requires_later_documents_first(self):
        opening=self.posted(self.stock())
        sale=self.posted(self.sale(),'documents')
        self.assertEqual(self.execute(opening,action='reverse').status_code,409)
        self.assertEqual(self.execute(sale,'documents','reverse').status_code,200)
        self.assertEqual(self.execute(opening,action='reverse').status_code,200)
        self.assertEqual(Balance.objects.get().quantity,0)
        self.assertEqual(Balance.objects.get().value,0)

    def test_reversal_is_idempotent_and_cannot_repeat_under_new_key(self):
        doc=self.posted(self.stock());key=uuid4()
        first=self.execute(doc,action='reverse',key=key)
        second=self.execute(doc,action='reverse',key=key)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(Movement.objects.filter(is_reversal=True).count(),1)
        self.assertEqual(self.execute(doc,action='reverse',version=3).status_code,409)

    def test_period_close_blocks_dates_and_only_owner_reopens(self):
        stock=self.stock()
        result=self.post(self.base+'/period/save',{'version':0,'closed_through':'2026-11-15','reason':'Close test'})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(self.execute(stock).status_code,409)
        Membership.objects.filter(pk=self.member.pk).update(role='accountant')
        self.assertEqual(self.post(self.base+'/period/save',{'version':1,'closed_through':None,'reason':'Reopen'}).status_code,403)
        Membership.objects.filter(pk=self.member.pk).update(role='owner')
        self.assertEqual(self.post(self.base+'/period/save',{'version':1,'closed_through':None,'reason':'Owner correction'}).status_code,200)
        self.assertEqual(PeriodEvent.objects.count(),2)
        self.assertEqual(self.execute(stock).status_code,200)

    def test_closed_period_blocks_current_reversal(self):
        doc=self.posted(self.stock())
        self.post(self.base+'/period/save',{'version':0,'closed_through':'2026-11-15','reason':'Close'})
        self.assertEqual(self.execute(doc,action='reverse').status_code,409)
        self.assertFalse(Posting.objects.get().is_reversed)

    def test_posting_permissions_and_warehouse_cost_redaction(self):
        doc=self.posted(self.stock())
        for role in ['manager','storekeeper','viewer']:
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            self.assertEqual(self.execute(doc,action='reverse').status_code,403)
            balance=self.client.get(self.base+'/balances').json()['items'][0]
            self.assertNotIn('value',balance)
            self.assertNotIn('average_cost',balance)
            movement=self.client.get(self.base+'/movements').json()['items'][0]
            self.assertNotIn('value',movement)
            self.assertEqual(self.client.get(self.base+'/period').status_code,403)

    def test_foreign_document_and_revoked_membership(self):
        doc=self.stock()
        foreign=f'/api/v1/organizations/{self.other.pk}'
        payload={'version':1,'idempotency_key':str(uuid4())}
        self.assertEqual(self.post(foreign+'/inventory/'+doc['id']+'/post',payload).status_code,403)
        Membership.objects.create(user=self.user,organization=self.other,role='owner')
        self.assertEqual(self.post(foreign+'/inventory/'+doc['id']+'/post',payload).status_code,404)
        Membership.objects.filter(pk=self.member.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.base+'/balances').status_code,403)
        self.assertEqual(self.execute(doc).status_code,403)

    def test_audit_failure_rolls_back_balance_status_movements_and_key(self):
        doc=self.stock()
        with patch('posting.services.StockEvent.objects.create',side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.execute(doc)
        self.assertFalse(Posting.objects.exists())
        self.assertFalse(Balance.objects.exists())
        self.assertFalse(Movement.objects.exists())
        self.assertFalse(RequestRecord.objects.exists())
        self.assertEqual(StockDraft.objects.get().status,'draft')

    def test_used_product_type_and_unit_cannot_be_changed_through_catalog(self):
        self.stock()
        result=self.client.post(self.base+'/catalog/products/'+str(self.goods.pk),{'name':'Changed','version':1,'kind':'service','unit':str(self.unit.pk),'price':'100'})
        self.assertEqual(result.status_code,409)
        self.goods.refresh_from_db();self.assertEqual(self.goods.kind,'goods')

    def test_csrf_mfa_and_page(self):
        doc=self.stock();client=Client()
        client.force_login(self.user)
        self.assertEqual(client.get(self.base+'/balances').status_code,401)
        self.assertEqual(client.get('/stock/').status_code,302)
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        session=client.session;session['mfa_started']=session['mfa_last']=time.time();session.save()
        self.assertEqual(client.post(self.base+'/inventory/'+doc['id']+'/post',data=json.dumps({'version':1,'idempotency_key':str(uuid4())}),content_type='application/json').status_code,403)
        self.assertEqual(self.client.get('/stock/').status_code,200)

    def test_request_and_period_validation(self):
        doc=self.stock()
        self.assertEqual(self.post(self.base+'/inventory/'+doc['id']+'/post',{'version':1}).status_code,400)
        self.assertEqual(self.post(self.base+'/period/save',{'version':0,'closed_through':'2026-11-16','reason':'Future'}).status_code,400)
        self.assertEqual(self.post(self.base+'/period/save',{'version':0,'closed_through':'2026-11-15','reason':''}).status_code,400)
        self.assertEqual(self.post(self.base+'/period/save',{'version':0,'closed_through':False,'reason':'Bad type'}).status_code,400)
        self.assertFalse(Period.objects.exists())
        self.assertEqual(self.client.get(self.base+'/balances?page=invalid').status_code,400)


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature


class PostingConcurrencyTests(TransactionTestCase):
    post=PostingTests.post
    stock=PostingTests.stock
    sale=PostingTests.sale
    execute=PostingTests.execute
    posted=PostingTests.posted

    def setUp(self):
        PostingTests.setUpTestData.__func__(type(self))
        PostingTests.setUp(self)
        self.clients=[Client(),Client()]
        for client in self.clients:
            client.force_login(self.user)
            session=client.session;session['mfa_started']=session['mfa_last']=time.time();session.save()

    def race(self,requests):
        barrier=Barrier(2)
        def invoke(index):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                url,data=requests[index]
                response=self.clients[index].post(url,data=json.dumps(data),content_type='application/json')
                return response.status_code,response.json()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(invoke,range(2)))

    @skipUnlessDBFeature('has_select_for_update')
    def test_only_one_sale_can_consume_last_unit(self):
        self.posted(self.stock(quantity='1'))
        docs=[self.sale(quantity='1'),self.sale(quantity='1')]
        requests=[(self.base+'/documents/'+doc['id']+'/post',{'version':1,'idempotency_key':str(uuid4())}) for doc in docs]
        results=self.race(requests)
        self.assertEqual(sorted(result[0] for result in results),[200,409])
        self.assertEqual(Balance.objects.get().quantity,0)
        self.assertEqual(Balance.objects.get().value,0)
        self.assertEqual(Document.objects.filter(status='posted').count(),1)
        self.assertEqual(Movement.objects.count(),2)

    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_same_key_creates_one_posting(self):
        doc=self.stock();key=str(uuid4())
        request=(self.base+'/inventory/'+doc['id']+'/post',{'version':1,'idempotency_key':key})
        results=self.race([request,request])
        self.assertEqual([result[0] for result in results],[200,200])
        self.assertEqual(results[0][1],results[1][1])
        self.assertEqual(Posting.objects.count(),1)
        self.assertEqual(Movement.objects.count(),1)
        self.assertEqual(RequestRecord.objects.count(),1)
