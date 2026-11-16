import json
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase
from catalog.models import Warehouse,Product
from accounts.models import Membership,Organization
from posting import tests as posting_tests
from posting.models import Balance,Movement,Posting,RequestRecord,Period
from .models import Operation,OperationLine


class OperationTests(TestCase):
    post=posting_tests.PostingTests.post
    stock=posting_tests.PostingTests.stock
    sale=posting_tests.PostingTests.sale
    execute=posting_tests.PostingTests.execute
    posted=posting_tests.PostingTests.posted
    setUp=posting_tests.PostingTests.setUp

    @classmethod
    def setUpTestData(cls):
        posting_tests.PostingTests.setUpTestData.__func__(cls)
        cls.target=Warehouse.objects.create(organization=cls.org,name='Target')
        cls.foreign=Warehouse.objects.create(organization=cls.other,name='Foreign')

    def operation(self,kind='writeoff',**extra):
        payload={'kind':kind,'date':'2026-11-15','warehouse':str(self.warehouse.pk),'target_warehouse':str(self.target.pk) if kind=='transfer' else None,'lines':[{'product':str(self.goods.pk),'quantity':'1'}],**extra}
        response=self.post(self.base+'/operations/add',payload)
        self.assertEqual(response.status_code,201,response.content)
        return response.json()['document']

    def return_document(self,sale,quantity='1'):
        return self.operation('return',source_sale=sale['id'],lines=[{'source_line':sale['lines'][0]['id'],'quantity':quantity}])

    def test_stock01_ten_sell_three_return_one_gives_eight(self):
        self.posted(self.stock())
        sale=self.posted(self.sale(),'documents')
        returned=self.return_document(sale)
        result=self.execute(returned,'operations')
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['posting']['cost_total'],'60.00')
        self.assertEqual(Balance.objects.get().quantity,Decimal('8'))
        self.assertEqual(Balance.objects.get().value,Decimal('480.00'))

    def test_return_uses_original_cost_after_new_receipt_changes_average(self):
        self.posted(self.stock())
        sale=self.posted(self.sale(),'documents')
        self.posted(self.stock(kind='receipt',price='100'))
        result=self.execute(self.return_document(sale),'operations')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['posting']['cost_total'],'60.00')
        self.assertEqual(Balance.objects.get().value,Decimal('1480.00'))
        self.assertEqual(Balance.objects.get().quantity,Decimal('18'))

    def test_cumulative_returns_cannot_exceed_sale_and_source_cannot_reverse_first(self):
        self.posted(self.stock())
        sale=self.posted(self.sale(),'documents')
        first=self.posted(self.return_document(sale,'2'),'operations')
        second=self.return_document(sale,'2')
        self.assertEqual(self.execute(second,'operations').status_code,409)
        self.assertEqual(self.execute(sale,'documents','reverse').status_code,409)
        self.assertEqual(Balance.objects.get().quantity,Decimal('9'))
        self.assertEqual(self.execute(first,'operations','reverse').status_code,200)
        self.assertEqual(self.execute(sale,'documents','reverse').status_code,200)
        self.assertEqual(Balance.objects.get().quantity,Decimal('10'))

    def test_full_return_preserves_all_residual_cents(self):
        self.posted(self.stock(quantity='0.003',price='1.67'))
        sale=self.posted(self.sale(quantity='0.003'),'documents')
        for _ in range(3):self.posted(self.return_document(sale,'0.001'),'operations')
        self.assertEqual(Balance.objects.get().quantity,Decimal('0.003'))
        self.assertEqual(Balance.objects.get().value,Decimal('0.01'))
        self.assertEqual(sum(OperationLine.objects.values_list('cost',flat=True)),Decimal('0.01'))

    def test_transfer_preserves_total_quantity_and_value(self):
        self.posted(self.stock())
        doc=self.operation('transfer',lines=[{'product':str(self.goods.pk),'quantity':'3'}])
        result=self.execute(doc,'operations')
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(Balance.objects.get(warehouse=self.warehouse).quantity,Decimal('7'))
        self.assertEqual(Balance.objects.get(warehouse=self.target).quantity,Decimal('3'))
        self.assertEqual(sum(Balance.objects.values_list('value',flat=True)),Decimal('600.00'))
        self.assertEqual(sum(Balance.objects.values_list('quantity',flat=True)),Decimal('10'))
        rows=Movement.objects.filter(posting__operation_id=doc['id'])
        self.assertEqual(rows.count(),2)
        self.assertEqual(sum(rows.values_list('value',flat=True)),0)

    def test_transfer_reversal_restores_both_warehouses_and_honors_later_moves(self):
        self.posted(self.stock())
        moved=self.posted(self.operation('transfer'),'operations')
        sold=self.sale(quantity='1')
        # Sale on destination warehouse creates a dependent movement.
        self.post(self.base+'/documents/'+sold['id']+'/save',{'version':1,'date':sold['date'],'counterparty':sold['counterparty'],'warehouse':str(self.target.pk),'lines':[{'product':str(self.goods.pk),'quantity':'1','price':'100'}]})
        sold=self.client.get(self.base+'/documents/'+sold['id']).json()['document']
        sold=self.posted(sold,'documents')
        self.assertEqual(self.execute(moved,'operations','reverse').status_code,409)
        self.assertEqual(self.execute(sold,'documents','reverse').status_code,200)
        self.assertEqual(self.execute(moved,'operations','reverse').status_code,200)
        self.assertEqual(Balance.objects.get(warehouse=self.warehouse).quantity,Decimal('10'))
        self.assertEqual(Balance.objects.get(warehouse=self.target).quantity,0)
        self.assertEqual(Balance.objects.get(warehouse=self.target).value,0)

    def test_writeoff_uses_average_and_rejects_negative_stock(self):
        self.posted(self.stock())
        self.posted(self.stock(kind='receipt',price='80'))
        result=self.execute(self.operation(lines=[{'product':str(self.goods.pk),'quantity':'3'}]),'operations')
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['posting']['cost_total'],'210.00')
        oversized=self.operation(lines=[{'product':str(self.goods.pk),'quantity':'18'}])
        self.assertEqual(self.execute(oversized,'operations').status_code,409)
        self.assertEqual(Balance.objects.get().quantity,Decimal('17'))

    def test_invalid_target_services_foreign_source_and_origin_rejected(self):
        for target in [self.warehouse,self.foreign]:
            response=self.post(self.base+'/operations/add',{'kind':'transfer','date':'2026-11-15','warehouse':str(self.warehouse.pk),'target_warehouse':str(target.pk),'lines':[{'product':str(self.goods.pk),'quantity':'1'}]})
            self.assertEqual(response.status_code,400)
        self.assertEqual(self.post(self.base+'/operations/add',{'kind':'writeoff','date':'2026-11-15','warehouse':str(self.warehouse.pk),'lines':[{'product':str(self.service.pk),'quantity':'1'}]}).status_code,400)
        draft_sale=self.sale()
        self.assertEqual(self.post(self.base+'/operations/add',{'kind':'return','date':'2026-11-15','source_sale':draft_sale['id'],'lines':[{'source_line':draft_sale['lines'][0]['id'],'quantity':'1'}]}).status_code,400)
        self.assertEqual(self.client.get(f'/api/v1/organizations/{self.other.pk}/return-sources').status_code,403)

    def test_return_sources_remaining_and_service_only_sale_excluded(self):
        self.posted(self.stock())
        goods_sale=self.posted(self.sale(),'documents')
        self.posted(self.sale(service=True),'documents')
        self.posted(self.return_document(goods_sale),'operations')
        sources=self.client.get(self.base+'/return-sources').json()['items']
        self.assertEqual([item['id'] for item in sources],[goods_sale['id']])
        rows=self.client.get(self.base+'/return-sources/'+goods_sale['id']).json()['items']
        self.assertEqual(rows[0]['remaining'],'2.000')

    def test_idempotency_and_transactional_audit_rollback(self):
        self.posted(self.stock())
        doc=self.operation('transfer');key=uuid4()
        first=self.execute(doc,'operations',key=key)
        second=self.execute(doc,'operations',key=key)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(Movement.objects.filter(posting__operation_id=doc['id']).count(),2)
        rollback=self.operation()
        quantity=Balance.objects.get(warehouse=self.warehouse).quantity
        with patch('operations.models.OperationEvent.objects.create',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):self.execute(rollback,'operations')
        self.assertEqual(Balance.objects.get(warehouse=self.warehouse).quantity,quantity)
        self.assertFalse(Posting.objects.filter(operation_id=rollback['id']).exists())
        self.assertEqual(Operation.objects.get(pk=rollback['id']).status,'draft')

    def test_permissions_and_cost_redaction(self):
        self.posted(self.stock())
        moved=self.posted(self.operation('transfer'),'operations')
        for role in ['storekeeper','manager','viewer']:
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            response=self.client.get(self.base+'/operations/'+moved['id'])
            self.assertEqual(response.status_code,200)
            self.assertNotIn('total',response.json()['document'])
            self.assertNotIn('cost',response.json()['document']['lines'][0])
            self.assertEqual(self.execute(moved,'operations','reverse').status_code,403)
        Membership.objects.filter(pk=self.member.pk).update(role='storekeeper')
        self.assertEqual(self.client.get(self.base+'/return-sources').status_code,403)
        self.operation('writeoff')
        Membership.objects.filter(pk=self.member.pk).update(role='manager')
        self.assertEqual(self.post(self.base+'/operations/add',{'kind':'writeoff'}).status_code,403)

    def test_versions_archive_and_immutable_posted_operations(self):
        doc=self.operation()
        data={'kind':'writeoff','date':'2026-11-15','warehouse':str(self.warehouse.pk),'version':1,'lines':[{'id':doc['lines'][0]['id'],'product':str(self.goods.pk),'quantity':'2'}]}
        self.assertEqual(self.post(self.base+'/operations/'+doc['id']+'/save',data).status_code,200)
        self.assertEqual(self.post(self.base+'/operations/'+doc['id']+'/save',data).status_code,409)
        self.assertEqual(self.post(self.base+'/operations/'+doc['id']+'/state',{'version':2,'is_active':False}).status_code,200)
        self.assertEqual(self.client.get(self.base+'/operations').json()['total'],0)
        self.assertEqual(self.post(self.base+'/operations/'+doc['id']+'/state',{'version':3,'is_active':True}).status_code,200)
        self.posted(self.stock())
        saved=self.client.get(self.base+'/operations/'+doc['id']).json()['document']
        posted=self.posted(saved,'operations')
        data['version']=posted['version']
        self.assertEqual(self.post(self.base+'/operations/'+doc['id']+'/save',data).status_code,409)

    def test_period_blocks_posting_and_page_requires_mfa(self):
        self.posted(self.stock())
        doc=self.operation()
        Period.objects.create(organization=self.org,closed_through='2026-11-15')
        self.assertEqual(self.execute(doc,'operations').status_code,409)
        self.assertEqual(self.client.get('/operations/').status_code,200)
        self.client.logout()
        self.assertEqual(self.client.get('/operations/').status_code,302)


from django.test import TransactionTestCase,skipUnlessDBFeature,Client
import time


class OperationConcurrencyTests(TransactionTestCase):
    post=OperationTests.post
    stock=OperationTests.stock
    sale=OperationTests.sale
    execute=OperationTests.execute
    posted=OperationTests.posted
    operation=OperationTests.operation
    return_document=OperationTests.return_document
    race=posting_tests.PostingConcurrencyTests.race

    def setUp(self):
        OperationTests.setUpTestData.__func__(type(self))
        posting_tests.PostingTests.setUp(self)
        self.clients=[Client(),Client()]
        for client in self.clients:
            client.force_login(self.user)
            session=client.session;session['mfa_started']=session['mfa_last']=time.time();session.save()

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_returns_cannot_restore_more_than_sold(self):
        self.posted(self.stock(quantity='1'))
        sale=self.posted(self.sale(quantity='1'),'documents')
        docs=[self.return_document(sale),self.return_document(sale)]
        requests=[(self.base+'/operations/'+doc['id']+'/post',{'version':1,'idempotency_key':str(uuid4())}) for doc in docs]
        results=self.race(requests)
        self.assertEqual(sorted(result[0] for result in results),[200,409])
        self.assertEqual(Balance.objects.get().quantity,Decimal('1'))
        self.assertEqual(Balance.objects.get().value,Decimal('60.00'))
        self.assertEqual(Operation.objects.filter(status='posted').count(),1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_transfers_cannot_consume_last_unit_twice(self):
        self.posted(self.stock(quantity='1'))
        docs=[self.operation('transfer'),self.operation('transfer')]
        requests=[(self.base+'/operations/'+doc['id']+'/post',{'version':1,'idempotency_key':str(uuid4())}) for doc in docs]
        results=self.race(requests)
        self.assertEqual(sorted(result[0] for result in results),[200,409])
        self.assertEqual(Balance.objects.get(warehouse=self.warehouse).quantity,0)
        self.assertEqual(Balance.objects.get(warehouse=self.target).quantity,Decimal('1'))
        self.assertEqual(sum(Balance.objects.values_list('value',flat=True)),Decimal('60.00'))
