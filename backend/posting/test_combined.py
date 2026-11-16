from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase
from accounts.models import Membership
from inventory.models import StockDraft,StockLine,StockSequence
from documents.models import Document
from . import tests as posting_tests
from .models import Balance,Movement,Posting,RequestRecord,Period


class CombinedActionTests(TestCase):
    post=posting_tests.PostingTests.post
    stock=posting_tests.PostingTests.stock
    sale=posting_tests.PostingTests.sale
    execute=posting_tests.PostingTests.execute
    posted=posting_tests.PostingTests.posted
    setUp=posting_tests.PostingTests.setUp
    setUpTestData=posting_tests.PostingTests.setUpTestData.__func__
    setUpTestData=classmethod(setUpTestData)

    def stock_payload(self,**extra):
        return {'kind':'receipt','date':'2026-11-15','counterparty':str(self.customer.pk),'warehouse':str(self.warehouse.pk),'lines':[{'product':str(self.goods.pk),'quantity':'10','price':'60.00'}],**extra}

    def combined(self,source,payload,document=None,key=None):
        route=self.base+'/'+source+('/'+document if document else '')+'/save-and-post'
        return self.post(route,{'document':payload,'idempotency_key':str(key or uuid4())})

    def test_new_receipt_is_saved_and_posted_in_one_action(self):
        result=self.combined('inventory',self.stock_payload())
        self.assertEqual(result.status_code,200,result.content)
        doc=result.json()['document']
        self.assertEqual(doc['status'],'posted')
        self.assertEqual(doc['total'],'600.00')
        self.assertEqual(Balance.objects.get().quantity,10)
        self.assertEqual(StockDraft.objects.count(),1)
        self.assertEqual(Posting.objects.count(),1)

    def test_changed_supplier_does_not_require_intermediate_draft_save(self):
        doc=self.stock(kind='receipt')
        StockDraft.objects.filter(pk=doc['id']).update(counterparty=None,counterparty_snapshot={})
        result=self.combined('inventory',self.stock_payload(version=doc['version'],note='Supplier selected'),doc['id'])
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['document']['counterparty'],str(self.customer.pk))
        self.assertEqual(result.json()['document']['status'],'posted')
        self.assertEqual(StockDraft.objects.count(),1)

    def test_missing_supplier_leaves_no_partial_document_or_number(self):
        response=self.combined('inventory',self.stock_payload(counterparty=None))
        self.assertEqual(response.status_code,400)
        self.assertFalse(StockDraft.objects.exists())
        self.assertFalse(StockLine.objects.exists())
        self.assertFalse(StockSequence.objects.exists())
        self.assertFalse(RequestRecord.objects.exists())
        self.assertFalse(Balance.objects.exists())

    def test_insufficient_sale_keeps_original_saved_draft_and_lines(self):
        self.posted(self.stock(quantity='2'))
        doc=self.sale(quantity='1')
        payload={'version':doc['version'],'date':doc['date'],'warehouse':doc['warehouse'],'counterparty':doc['counterparty'],'note':'Must not persist','lines':[{'id':doc['lines'][0]['id'],'product':str(self.goods.pk),'quantity':'3','price':'100'}]}
        response=self.combined('documents',payload,doc['id'])
        self.assertEqual(response.status_code,409)
        saved=Document.objects.get(pk=doc['id'])
        self.assertEqual(saved.version,1)
        self.assertEqual(saved.status,'draft')
        self.assertEqual(saved.note,doc['note'])
        self.assertEqual(str(saved.lines.get().pk),doc['lines'][0]['id'])
        self.assertEqual(saved.lines.get().quantity,1)
        self.assertEqual(Balance.objects.get().quantity,2)

    def test_new_combined_retry_is_idempotent_and_changed_body_conflicts(self):
        key=uuid4();payload=self.stock_payload()
        first=self.combined('inventory',payload,key=key)
        second=self.combined('inventory',payload,key=key)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(StockDraft.objects.count(),1)
        self.assertEqual(Movement.objects.count(),1)
        self.assertEqual(self.combined('inventory',{**payload,'note':'Changed'},key=key).status_code,409)

    def test_new_sale_and_operation_can_save_and_post_directly(self):
        self.posted(self.stock())
        sale={'date':'2026-11-15','counterparty':str(self.customer.pk),'warehouse':str(self.warehouse.pk),'lines':[{'product':str(self.goods.pk),'quantity':'3','price':'100'}]}
        result=self.combined('documents',sale)
        self.assertEqual(result.status_code,200,result.content)
        sold=result.json()['document']
        returned={'kind':'return','date':'2026-11-15','source_sale':sold['id'],'lines':[{'source_line':sold['lines'][0]['id'],'quantity':'1'}]}
        result=self.combined('operations',returned)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['document']['status'],'posted')
        self.assertEqual(Balance.objects.get().quantity,8)
        self.assertEqual(Balance.objects.get().value,480)

    def test_manager_and_storekeeper_cannot_use_combined_posting(self):
        for role in ['manager','storekeeper','viewer']:
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            self.assertEqual(self.combined('inventory',self.stock_payload()).status_code,403)
        self.assertFalse(StockDraft.objects.exists())

    def test_closed_date_and_audit_failure_roll_back_entire_action(self):
        Period.objects.create(organization=self.org,closed_through='2026-11-15')
        self.assertEqual(self.combined('inventory',self.stock_payload()).status_code,409)
        self.assertFalse(StockDraft.objects.exists())
        Period.objects.all().delete()
        with patch('posting.services.StockEvent.objects.create',side_effect=RuntimeError('Audit failed')):
            with self.assertRaises(RuntimeError):self.combined('inventory',self.stock_payload())
        self.assertFalse(StockDraft.objects.exists())
        self.assertFalse(Posting.objects.exists())
        self.assertFalse(Balance.objects.exists())

    def test_stale_existing_draft_and_posted_document_cannot_be_overwritten(self):
        doc=self.stock(kind='receipt')
        self.assertEqual(self.combined('inventory',self.stock_payload(version=0),doc['id']).status_code,409)
        posted=self.posted(doc)
        self.assertEqual(self.combined('inventory',self.stock_payload(version=posted['version']),doc['id']).status_code,409)
        self.assertEqual(StockDraft.objects.get().version,posted['version'])


from django.test import TransactionTestCase,skipUnlessDBFeature


class CombinedConcurrencyTests(TransactionTestCase):
    setUp=posting_tests.PostingConcurrencyTests.setUp
    race=posting_tests.PostingConcurrencyTests.race
    stock_payload=CombinedActionTests.stock_payload

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_simultaneous_new_requests_create_one_document_and_posting(self):
        request=(self.base+'/inventory/save-and-post',{'document':self.stock_payload(),'idempotency_key':str(uuid4())})
        results=self.race([request,request])
        self.assertEqual([result[0] for result in results],[200,200])
        self.assertEqual(results[0][1],results[1][1])
        self.assertEqual(StockDraft.objects.count(),1)
        self.assertEqual(Posting.objects.count(),1)
        self.assertEqual(RequestRecord.objects.count(),1)
        self.assertEqual(StockSequence.objects.get().last_number,1)
        self.assertEqual(Balance.objects.get().quantity,10)
