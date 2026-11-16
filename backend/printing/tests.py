import time
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase,Client
from accounts.models import Membership
from documents.models import Document,DocumentEvent
from posting import tests as fixture
from posting.models import Balance,Movement,Posting
from .models import IssuerProfile,ProfileEvent,PrintEvent
from .profiles import snapshot


class PrintingTests(TestCase):
    setUpTestData=classmethod(fixture.PostingTests.setUpTestData.__func__)
    setUp=fixture.PostingTests.setUp
    post=fixture.PostingTests.post
    stock=fixture.PostingTests.stock
    sale=fixture.PostingTests.sale
    execute=fixture.PostingTests.execute
    posted=fixture.PostingTests.posted

    def profile_data(self,**extra):
        return {'version':0,'legal_name':'ТОВ Тестова організація','kind':'company','code':'12345678','address':'Дніпро, тестова адреса','bank_details':'IBAN UA000000000000000000000000000','email':'office@example.com','phone':'+380000000000','signatory':'Тестовий підписант',**extra}

    def profile(self,**extra):
        response=self.post(self.base+'/issuer-profile/save',self.profile_data(**extra))
        self.assertEqual(response.status_code,200,response.content)
        return response.json()

    def preview(self,document,kind='invoice',query=''):
        return self.client.get(f'/print/{self.org.pk}/{document["id"]}/{kind}/'+query)

    def mixed(self):
        self.posted(self.stock())
        payload={'date':'2026-11-15','warehouse':str(self.warehouse.pk),'counterparty':str(self.customer.pk),'lines':[{'product':str(self.goods.pk),'quantity':'3','price':'100'},{'product':str(self.service.pk),'quantity':'2','price':'1500'}]}
        response=self.post(self.base+'/documents/save-and-post',{'document':payload,'idempotency_key':'a9aa9667-7cad-487b-90e5-fa038e539353'})
        self.assertEqual(response.status_code,200,response.content)
        return response.json()['document']

    def test_profile_defaults_save_version_and_audit(self):
        result=self.client.get(self.base+'/issuer-profile').json()
        self.assertFalse(result['configured']);self.assertEqual(result['version'],0)
        self.assertEqual(result['profile']['legal_name'],'A')
        self.assertFalse(IssuerProfile.objects.exists())
        saved=self.profile()
        self.assertEqual(saved['version'],1)
        self.assertEqual(self.post(self.base+'/issuer-profile/save',self.profile_data()).status_code,409)
        saved=self.profile(version=1,legal_name='Нові реквізити')
        self.assertEqual(saved['version'],2)
        self.assertEqual(ProfileEvent.objects.count(),2)
        self.assertEqual(ProfileEvent.objects.last().before['legal_name'],'ТОВ Тестова організація')
        self.assertEqual(self.client.get('/organization-details/').status_code,200)

    def test_profile_validates_types_email_unknown_fields_and_rollback(self):
        for extra in [{'kind':'unknown'},{'email':'not-email'},{'legal_name':''},{'version':True},{'unknown':'x'}]:
            self.assertIn(self.post(self.base+'/issuer-profile/save',self.profile_data(**extra)).status_code,[400,409])
        self.assertFalse(IssuerProfile.objects.exists())
        with patch('printing.profiles.ProfileEvent.objects.create',side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):self.post(self.base+'/issuer-profile/save',self.profile_data())
        self.assertFalse(IssuerProfile.objects.exists())

    def test_profile_permissions_isolation_and_csrf(self):
        for role in ['manager','viewer','storekeeper']:
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            self.assertEqual(self.post(self.base+'/issuer-profile/save',self.profile_data()).status_code,403)
        Membership.objects.filter(pk=self.member.pk).update(role='accountant')
        self.profile()
        self.assertEqual(self.client.get(f'/api/v1/organizations/{self.other.pk}/issuer-profile').status_code,403)
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        session=client.session;session['mfa_started']=session['mfa_last']=time.time();session.save()
        self.assertEqual(client.post(self.base+'/issuer-profile/save',self.profile_data()).status_code,403)

    def test_draft_invoice_is_marked_and_delivery_act_need_posting(self):
        self.profile();draft=self.sale(service=True)
        response=self.preview(draft)
        self.assertContains(response,'ЧЕРНЕТКА')
        self.assertContains(response,'ПДВ не розраховано')
        self.assertContains(response,'Не фіскальний чек')
        self.assertEqual(self.preview(draft,'act').status_code,409)
        self.assertEqual(self.preview(draft,'delivery').status_code,409)
        self.assertEqual(PrintEvent.objects.count(),1)
        self.assertFalse(Posting.objects.exists())

    def test_mixed_document_prints_correct_subsets_and_totals(self):
        self.profile();document=self.mixed()
        invoice=self.preview(document);delivery=self.preview(document,'delivery');act=self.preview(document,'act')
        self.assertContains(invoice,'3\u00a0300,00')
        self.assertContains(delivery,'300,00');self.assertNotContains(delivery,'3\u00a0000,00')
        self.assertContains(act,'3\u00a0000,00');self.assertNotContains(act,'>Товар<')
        self.assertContains(delivery,'лише товарну частину')
        self.assertContains(act,'лише послуги')
        self.assertEqual(PrintEvent.objects.count(),3)
        self.assertEqual(PrintEvent.objects.last().issuer_source,'snapshot')

    def test_issuer_is_refreshed_at_post_then_frozen(self):
        self.profile();draft=self.sale(service=True)
        self.profile(version=1,legal_name='На дату проведення')
        posted=self.posted(draft,'documents')
        self.assertEqual(Document.objects.get(pk=posted['id']).issuer_snapshot['legal_name'],'На дату проведення')
        self.profile(version=2,legal_name='Пізніша назва')
        response=self.preview(posted,'act')
        self.assertContains(response,'На дату проведення')
        self.assertNotContains(response,'Пізніша назва')

    def test_old_missing_snapshot_is_explicit_current_fallback_without_mutation(self):
        document=self.posted(self.sale(service=True),'documents')
        self.profile()
        response=self.preview(document,'act')
        self.assertContains(response,'Історичного знімка реквізитів організації немає')
        self.assertContains(response,'ТОВ Тестова організація')
        self.assertEqual(Document.objects.get(pk=document['id']).issuer_snapshot,{})
        self.assertEqual(Document.objects.get(pk=document['id']).version,document['version'])
        self.assertEqual(PrintEvent.objects.get().issuer_source,'current')

    def test_missing_profile_does_not_create_fake_requisites(self):
        document=self.sale(service=True)
        response=self.preview(document)
        self.assertContains(response,'Реквізити організації неповні')
        self.assertNotContains(response,'UA000')
        self.assertFalse(IssuerProfile.objects.exists())

    def test_stale_version_reverse_status_empty_subsets_and_unknown_forms(self):
        document=self.posted(self.sale(service=True),'documents')
        self.assertEqual(self.preview(document,query='?version=1').status_code,409)
        self.assertEqual(self.preview(document,query='?version=nope').status_code,400)
        self.assertEqual(self.preview(document,'delivery').status_code,400)
        self.assertEqual(self.preview(document,'unknown').status_code,400)
        self.assertEqual(self.execute(document,'documents','reverse').status_code,200)
        self.assertEqual(self.preview(document,'act').status_code,409)
        self.assertContains(self.preview(document),'СТОРНОВАНИЙ ДОКУМЕНТ')

    def test_snapshot_names_and_decimal_values_are_used_without_catalog_replacement(self):
        from catalog.models import Product,Counterparty
        self.profile();draft=self.sale(service=True,quantity='0.003')
        Product.objects.filter(pk=self.service.pk).update(name='Changed service',price='999')
        Counterparty.objects.filter(pk=self.customer.pk).update(name='Changed buyer')
        response=self.preview(draft)
        self.assertContains(response,'Послуга');self.assertNotContains(response,'Changed service');self.assertNotContains(response,'Changed buyer')
        self.assertContains(response,'0,003');self.assertContains(response,'0,30')

    def test_html_escapes_business_fields_and_print_does_not_change_ledger(self):
        self.profile(legal_name='<script>alert(1)</script>')
        document=self.mixed();doc=Document.objects.get(pk=document['id'])
        before=(doc.version,Movement.objects.count(),Balance.objects.get().quantity,DocumentEvent.objects.count())
        response=self.preview(document)
        self.assertContains(response,'&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response,'<script>alert(1)</script>')
        doc.refresh_from_db()
        self.assertEqual((doc.version,Movement.objects.count(),Balance.objects.get().quantity,DocumentEvent.objects.count()),before)
        self.assertIn('no-store',response['Cache-Control'])

    def test_foreign_document_mfa_and_financial_visibility(self):
        document=self.posted(self.sale(service=True),'documents')
        for role in ['manager','viewer']:
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            self.assertEqual(self.preview(document,'act').status_code,200)
        Membership.objects.filter(pk=self.member.pk).update(role='storekeeper')
        self.assertEqual(self.preview(document).status_code,403)
        Membership.objects.filter(pk=self.member.pk).update(role='owner')
        self.assertEqual(self.client.get(f'/print/{self.other.pk}/{document["id"]}/invoice/').status_code,403)
        Membership.objects.create(user=self.user,organization=self.other,role='owner')
        self.assertEqual(self.client.get(f'/print/{self.other.pk}/{document["id"]}/invoice/').status_code,404)
        self.assertEqual(Client().get(f'/print/{self.org.pk}/{document["id"]}/invoice/').status_code,302)
        self.assertEqual(Client().get(self.base+'/issuer-profile').status_code,401)

    def test_corrupt_saved_totals_and_print_audit_failure_do_not_emit_success(self):
        document=self.sale(service=True)
        Document.objects.filter(pk=document['id']).update(total=Decimal('999'))
        self.assertEqual(self.preview(document).status_code,409)
        self.assertFalse(PrintEvent.objects.exists())
        Document.objects.filter(pk=document['id']).update(total=Decimal('300'))
        with patch('printing.views.PrintEvent.objects.create',side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):self.preview(document)
        self.assertFalse(PrintEvent.objects.exists())


from django.test import TransactionTestCase,skipUnlessDBFeature
from uuid import uuid4


class PrintConcurrencyTests(TransactionTestCase):
    setUp=fixture.PostingConcurrencyTests.setUp
    race=fixture.PostingConcurrencyTests.race
    post=PrintingTests.post
    sale=PrintingTests.sale
    profile=PrintingTests.profile
    profile_data=PrintingTests.profile_data

    @skipUnlessDBFeature('has_select_for_update')
    def test_profile_version_prevents_two_simultaneous_updates(self):
        self.profile()
        route=self.base+'/issuer-profile/save'
        results=self.race([(route,self.profile_data(version=1,legal_name='First')),(route,self.profile_data(version=1,legal_name='Second'))])
        self.assertEqual(sorted(r[0] for r in results),[200,409])
        self.assertEqual(IssuerProfile.objects.get().version,2)
        self.assertEqual(ProfileEvent.objects.count(),2)

    @skipUnlessDBFeature('has_select_for_update')
    def test_posting_freezes_one_consistent_profile_during_edit(self):
        self.profile()
        document=self.sale(service=True)
        results=self.race([(self.base+'/issuer-profile/save',self.profile_data(version=1,legal_name='New issuer',address='New address')),(self.base+'/documents/'+document['id']+'/post',{'version':document['version'],'idempotency_key':str(uuid4())})])
        self.assertEqual([r[0] for r in results],[200,200])
        snap=Document.objects.get(pk=document['id']).issuer_snapshot
        expected={1:('ТОВ Тестова організація','Дніпро, тестова адреса'),2:('New issuer','New address')}
        self.assertEqual((snap['legal_name'],snap['address']),expected[snap['profile_version']])
