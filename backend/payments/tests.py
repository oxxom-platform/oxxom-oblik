from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase, Client
from accounts.models import Membership
from catalog.models import Counterparty
from documents.models import Document
from posting import tests as fixture
from posting.models import Period, RequestRecord
from .models import MoneyAccount, Payment, Allocation, MoneyMovement, PaymentEvent, PaymentSequence
from .services import balance, settlement


class MoneyTests(TestCase):
    setUpTestData = classmethod(fixture.PostingTests.setUpTestData.__func__)
    setUp = fixture.PostingTests.setUp
    post = fixture.PostingTests.post
    stock = fixture.PostingTests.stock
    sale = fixture.PostingTests.sale
    execute = fixture.PostingTests.execute
    posted = fixture.PostingTests.posted

    def account(self,**extra):
        response=self.post(self.base+'/money-accounts/add',{'name':'Основна каса','kind':'cash',**extra})
        self.assertEqual(response.status_code,201,response.content)
        return response.json()['item']

    def payload(self,account=None,**extra):
        return {'direction':'in','date':'2026-11-15','account':(account or self.account())['id'],'counterparty':str(self.customer.pk),'amount':'1000.00','purpose':'Оплата','allocations':[],**extra}

    def pay(self,payload,payment=None,key=None,combined=True):
        path=self.base+'/payments'+('/'+payment if payment else '')+('/save-and-post' if combined else '/save' if payment else '/add')
        return self.post(path,{'payment':payload,'idempotency_key':str(key or uuid4())} if combined else payload)

    def command(self,payment,action,**extra):
        return self.post(self.base+'/payments/'+payment['id']+'/'+action,{'version':payment['version'],'idempotency_key':str(uuid4()),**extra})

    def test_money_01_partial_full_and_reverse_first_payment(self):
        sale=self.posted(self.sale(quantity='30',service=True),'documents')
        account=self.account()
        allocation={'source':'sale','document':sale['id'],'amount':'1000.00'}
        first=self.pay(self.payload(account,allocations=[allocation])).json()['payment']
        self.assertEqual(self.client.get(self.base+'/debts').json()['items'][0]['due'],'2000.00')
        second=self.pay(self.payload(account,amount='2000.00',allocations=[{**allocation,'amount':'2000.00'}]))
        self.assertEqual(second.status_code,200,second.content)
        document=Document.objects.get(pk=sale['id'])
        self.assertEqual(settlement(document)['payment_status'],'paid')
        self.assertEqual(self.command(first,'reverse').status_code,200)
        self.assertEqual(settlement(document)['due'],'1000.00')
        self.assertEqual(balance(MoneyAccount.objects.get()),Decimal('2000'))
        self.assertEqual(MoneyMovement.objects.count(),3)

    def test_draft_has_no_cash_or_settlement_and_can_be_posted(self):
        self.assertEqual(self.client.get('/payments/').status_code,200)
        sale=self.posted(self.sale(service=True),'documents')
        payload=self.payload(amount='100',allocations=[{'source':'sale','document':sale['id'],'amount':'100'}])
        response=self.pay(payload,combined=False)
        self.assertEqual(response.status_code,201,response.content)
        draft=response.json()['payment']
        self.assertFalse(MoneyMovement.objects.exists())
        self.assertEqual(settlement(Document.objects.get())['paid'],'0.00')
        self.assertEqual(self.command(draft,'post').status_code,200)
        self.assertEqual(settlement(Document.objects.get())['paid'],'100.00')

    def test_payment_across_documents_and_advance_then_redistribution(self):
        first=self.posted(self.sale(service=True),'documents')
        second=self.posted(self.sale(service=True),'documents')
        payload=self.payload(allocations=[{'source':'sale','document':first['id'],'amount':'100'},{'source':'sale','document':second['id'],'amount':'200'}])
        payment=self.pay(payload).json()['payment']
        self.assertEqual(payment['advance'],'700.00')
        before=balance(MoneyAccount.objects.get())
        response=self.command(payment,'allocate',allocations=[{'source':'sale','document':first['id'],'amount':'300'}])
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['payment']['advance'],'700.00')
        self.assertEqual(settlement(Document.objects.get(pk=second['id']))['paid'],'0.00')
        self.assertEqual(balance(MoneyAccount.objects.get()),before)
        self.assertTrue(PaymentEvent.objects.filter(action='allocate',details__before__0__amount='100.00').exists())

    def test_overallocation_duplicate_source_and_overpayment_roll_back(self):
        sale=self.posted(self.sale(service=True),'documents') # 300
        allocation={'source':'sale','document':sale['id'],'amount':'301'}
        account=self.account()
        for amount,rows in [('1000',[allocation]),('100',[{**allocation,'amount':'200'}]),('1000',[{**allocation,'amount':'1'}]*2)]:
            response=self.pay(self.payload(account,amount=amount,allocations=rows))
            self.assertIn(response.status_code,(400,409),response.content)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(PaymentSequence.objects.exists())
        self.assertFalse(MoneyMovement.objects.exists())

    def test_drafts_do_not_reserve_due_and_second_post_is_revalidated(self):
        sale=self.posted(self.sale(service=True),'documents')
        payload=self.payload(amount='300',allocations=[{'source':'sale','document':sale['id'],'amount':'300'}])
        first=self.pay(payload,combined=False).json()['payment']
        second=self.pay(payload,combined=False).json()['payment']
        self.assertEqual(self.command(first,'post').status_code,200)
        self.assertEqual(self.command(second,'post').status_code,409)
        self.assertEqual(Payment.objects.get(pk=second['id']).status,'draft')
        self.assertEqual(MoneyMovement.objects.count(),1)

    def test_supplier_payout_and_insufficient_balance(self):
        receipt=self.posted(self.stock(kind='receipt'))
        account=self.account()
        out=self.payload(account,direction='out',amount='500',allocations=[{'source':'receipt','document':receipt['id'],'amount':'500'}])
        self.assertEqual(self.pay(out).status_code,409)
        self.assertFalse(Payment.objects.exists())
        self.pay(self.payload(account,amount='1000'))
        result=self.pay(out)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(balance(MoneyAccount.objects.get()),500)
        self.assertEqual(self.client.get(self.base+'/debts?direction=out').json()['items'][0]['due'],'100.00')

    def test_reversal_checks_remaining_cash_and_preserves_allocation_history(self):
        account=self.account();incoming=self.pay(self.payload(account)).json()['payment']
        out=self.pay(self.payload(account,direction='out',amount='800')).json()['payment']
        self.assertEqual(self.command(incoming,'reverse').status_code,409)
        self.assertEqual(self.command(out,'reverse').status_code,200)
        self.assertEqual(self.command(incoming,'reverse').status_code,200)
        self.assertEqual(balance(MoneyAccount.objects.get()),0)
        self.assertEqual(MoneyMovement.objects.count(),4)

    def test_sources_and_account_are_scoped_and_direction_checked(self):
        account=self.account()
        foreign=MoneyAccount.objects.create(organization=self.other,name='Secret',kind='bank')
        cp=Counterparty.objects.create(organization=self.org,name='Other client',kind='company')
        sale=self.posted(self.sale(service=True),'documents')
        row={'source':'sale','document':sale['id'],'amount':'10'}
        for extra in [{'account':str(foreign.pk)},{'counterparty':str(cp.pk),'allocations':[row]},{'direction':'out','allocations':[row]},{'allocations':[{**row,'document':str(uuid4())}]}]:
            self.assertEqual(self.pay({**self.payload(account),**extra}).status_code,400)
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(self.client.get(self.base+'/money-accounts').json()['total'],1)
        self.assertEqual(self.client.get(f'/api/v1/organizations/{self.other.pk}/money-accounts').status_code,403)

    def test_nonfinancial_roles_and_mfa_csrf(self):
        account=self.account();payment=self.pay(self.payload(account)).json()['payment']
        for role in ('manager','storekeeper','viewer'):
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            for endpoint in ('payments','money-accounts','debts','money-movements','payment-events','payments/'+payment['id']):
                self.assertEqual(self.client.get(self.base+'/'+endpoint).status_code,403)
            self.assertEqual(self.pay(self.payload(account)).status_code,403)
        Membership.objects.filter(pk=self.member.pk).update(role='owner')
        session=self.client.session;session.pop('mfa_started',None);session.pop('mfa_last',None);session.save()
        self.assertEqual(self.client.get(self.base+'/payments').status_code,401)
        csrf_client=Client(enforce_csrf_checks=True);csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.base+'/payments/add',{},content_type='application/json').status_code,403)

    def test_idempotent_create_reverse_allocation_and_changed_body_conflict(self):
        payload=self.payload();key=uuid4()
        first=self.pay(payload,key=key)
        self.assertEqual(first.status_code,200,first.content)
        self.assertEqual(self.pay(payload,key=key).json(),first.json())
        self.assertEqual(self.pay({**payload,'purpose':'Other'},key=key).status_code,409)
        payment=first.json()['payment'];key2=str(uuid4())
        route=self.base+'/payments/'+payment['id']+'/allocate'
        body={'version':payment['version'],'allocations':[],'idempotency_key':key2}
        allocated=self.post(route,body)
        self.assertEqual(self.post(route,body).json(),allocated.json())
        payment=allocated.json()['payment'];route=self.base+'/payments/'+payment['id']+'/reverse'
        body={'version':payment['version'],'idempotency_key':str(uuid4())}
        reversed=self.post(route,body)
        self.assertEqual(self.post(route,body).json(),reversed.json())
        self.assertEqual(MoneyMovement.objects.count(),2)

    def test_source_reversal_blocked_until_payment_distribution_released(self):
        sale=self.posted(self.sale(service=True),'documents')
        payment=self.pay(self.payload(amount='100',allocations=[{'source':'sale','document':sale['id'],'amount':'100'}])).json()['payment']
        self.assertEqual(self.execute(sale,'documents','reverse').status_code,409)
        self.assertEqual(self.command(payment,'allocate',allocations=[]).status_code,200)
        self.assertEqual(self.execute(sale,'documents','reverse').status_code,200)

    def test_closed_future_backdated_and_audit_failures_are_atomic(self):
        payload=self.payload()
        Period.objects.create(organization=self.org,closed_through='2026-11-15')
        self.assertEqual(self.pay(payload).status_code,409)
        Period.objects.all().delete()
        self.assertEqual(self.pay({**payload,'date':'2026-11-16'}).status_code,400)
        with patch('payments.services.PaymentEvent.objects.create',side_effect=RuntimeError('Audit failed')):
            with self.assertRaises(RuntimeError): self.pay(payload)
        self.assertFalse(Payment.objects.exists());self.assertFalse(MoneyMovement.objects.exists())
        self.pay(payload)
        self.assertEqual(self.pay({**payload,'date':'2026-11-14'}).status_code,409)

    def test_stale_draft_posted_immutable_archival_and_snapshots(self):
        account=self.account()
        payload=self.payload(account)
        draft=self.pay(payload,combined=False).json()['payment']
        self.assertEqual(self.pay({**payload,'version':0},draft['id'],combined=False).status_code,409)
        MoneyAccount.objects.filter(pk=account['id']).update(name='Renamed')
        Counterparty.objects.filter(pk=self.customer.pk).update(name='Renamed client')
        updated=self.pay({**payload,'version':draft['version']},draft['id'],combined=False).json()['payment']
        self.assertEqual(updated['account_name'],'Основна каса')
        self.assertEqual(updated['counterparty_snapshot']['name'],'Client')
        posted=self.command(updated,'post').json()['payment']
        self.assertEqual(self.pay({**payload,'version':posted['version']},posted['id'],combined=False).status_code,409)
        self.assertEqual(self.command(posted,'state',is_active=False).status_code,400)
        self.assertEqual(self.post(self.base+'/money-accounts/'+account['id']+'/save',{**account,'kind':'bank'}).status_code,409)

    def test_decimal_inputs_reject_float_negative_and_excess_precision(self):
        account=self.account()
        for amount in (1.1,'0','-1','1.001','NaN','Infinity','1000000000000000000.001'):
            self.assertEqual(self.pay(self.payload(account,amount=amount)).status_code,400)
        self.assertFalse(Payment.objects.exists())

    def test_advance_can_be_allocated_after_later_sale_and_closed_distribution_blocks(self):
        payment=self.pay(self.payload()).json()['payment']
        sale=self.posted(self.sale(service=True),'documents')
        response=self.command(payment,'allocate',allocations=[{'source':'sale','document':sale['id'],'amount':'100'}])
        self.assertEqual(response.status_code,200,response.content)
        payment=response.json()['payment']
        Period.objects.create(organization=self.org,closed_through='2026-11-15')
        self.assertEqual(self.command(payment,'allocate',allocations=[]).status_code,409)
        self.assertEqual(settlement(Document.objects.get())['paid'],'100.00')

    def test_new_lookups_start_empty_and_archived_accounts_not_offered(self):
        self.assertEqual(self.client.get(self.base+'/money-accounts').json()['items'],[])
        account=self.account()
        response=self.post(self.base+'/money-accounts/'+account['id']+'/save',{**account,'is_active':False})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.client.get(self.base+'/money-accounts').json()['items'],[])
        self.assertEqual(self.pay(self.payload(account)).status_code,400)


from django.test import TransactionTestCase, skipUnlessDBFeature


class MoneyConcurrencyTests(TransactionTestCase):
    setUp=fixture.PostingConcurrencyTests.setUp
    race=fixture.PostingConcurrencyTests.race
    post=fixture.PostingTests.post
    sale=fixture.PostingTests.sale
    execute=fixture.PostingTests.execute
    posted=fixture.PostingTests.posted

    @skipUnlessDBFeature('has_select_for_update')
    def test_duplicate_new_payment_is_exactly_once(self):
        account=MoneyAccount.objects.create(organization=self.org,name='Cash',kind='cash')
        payload={'direction':'in','date':'2026-11-15','account':str(account.pk),'counterparty':str(self.customer.pk),'amount':'1000','purpose':'Pay','allocations':[]}
        request=(self.base+'/payments/save-and-post',{'payment':payload,'idempotency_key':str(uuid4())})
        results=self.race([request,request])
        self.assertEqual([r[0] for r in results],[200,200])
        self.assertEqual(results[0][1],results[1][1])
        self.assertEqual(Payment.objects.count(),1)
        self.assertEqual(MoneyMovement.objects.count(),1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_competing_payouts_cannot_spend_last_balance_twice(self):
        account=MoneyAccount.objects.create(organization=self.org,name='Cash',kind='cash')
        from .services import write_draft,post_payment
        from django.db import transaction
        payload={'direction':'in','date':'2026-11-15','account':str(account.pk),'counterparty':str(self.customer.pk),'amount':'1000','purpose':'Pay','allocations':[]}
        with transaction.atomic():
            org=type(self.org).objects.select_for_update().get(pk=self.org.pk)
            post_payment(org,self.user,write_draft(org,self.user,payload))
        requests=[(self.base+'/payments/save-and-post',{'payment':{**payload,'direction':'out'},'idempotency_key':str(uuid4())}) for _ in range(2)]
        results=self.race(requests)
        self.assertEqual(sorted(r[0] for r in results),[200,409])
        self.assertEqual(balance(account),0)

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_payments_cannot_both_pay_same_remaining_debt(self):
        sale=self.posted(self.sale(quantity='1',service=True),'documents')
        account=MoneyAccount.objects.create(organization=self.org,name='Cash',kind='cash')
        payload={'direction':'in','date':'2026-11-15','account':str(account.pk),'counterparty':str(self.customer.pk),'amount':'100','purpose':'Pay','allocations':[{'source':'sale','document':sale['id'],'amount':'100'}]}
        requests=[(self.base+'/payments/save-and-post',{'payment':payload,'idempotency_key':str(uuid4())}) for _ in range(2)]
        results=self.race(requests)
        self.assertEqual(sorted(r[0] for r in results),[200,409])
        self.assertEqual(settlement(Document.objects.get())['paid'],'100.00')
        self.assertEqual(Payment.objects.count(),1)
