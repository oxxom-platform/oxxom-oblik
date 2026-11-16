import csv
import io
from decimal import Decimal
from unittest.mock import patch,MagicMock
from django.test import TestCase,Client
from accounts.models import Membership
from catalog.models import Counterparty, Warehouse, Product
from documents.models import Document
from posting import tests as fixture
from payments import tests as money_fixture
from posting.models import Posting
from .views import csv_text


class ReportTests(TestCase):
    setUpTestData=classmethod(fixture.PostingTests.setUpTestData.__func__)
    setUp=fixture.PostingTests.setUp
    post=fixture.PostingTests.post
    stock=fixture.PostingTests.stock
    sale=fixture.PostingTests.sale
    execute=fixture.PostingTests.execute
    posted=fixture.PostingTests.posted
    account=money_fixture.MoneyTests.account
    payload=money_fixture.MoneyTests.payload
    pay=money_fixture.MoneyTests.pay
    command=money_fixture.MoneyTests.command

    def report(self,kind,query='',export=False):
        return self.client.get(self.base+'/reports/'+kind+('/export' if export else '')+query)

    def test_sales_exclude_drafts_reversed_and_keep_snapshots(self):
        self.sale(service=True)
        posted=self.posted(self.sale(service=True),'documents')
        reversed=self.posted(self.sale(service=True),'documents')
        self.execute(reversed,'documents','reverse')
        Counterparty.objects.filter(pk=self.customer.pk).update(name='Renamed')
        response=self.report('sales')
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()
        self.assertEqual(data['total'],1)
        self.assertEqual(data['summary']['total'],'300.00')
        self.assertEqual(data['items'][0]['number'],posted['number'])
        self.assertEqual(data['items'][0]['counterparty'],'Client')
        self.assertIn('не відновлення історичного',data['note'])

    def test_margin_uses_fixed_cost_and_is_explicitly_provisional(self):
        self.posted(self.stock())
        self.posted(self.sale(),'documents')
        data=self.report('margin').json()
        self.assertEqual(data['summary'],{'total':'300.00','cost':'180.00','margin':'120.00'})
        self.assertEqual(data['items'][0]['cost'],'180.00')
        self.assertIn('Не фінальний прибуток',data['note'])
        self.posted(self.stock(price='90',kind='receipt'))
        self.assertEqual(self.report('margin').json()['summary']['cost'],'180.00')

    def test_stock_balances_and_journal_include_reversal_separately(self):
        self.posted(self.stock())
        sale=self.posted(self.sale(),'documents')
        data=self.report('balances').json()
        self.assertEqual(data['summary']['value'],'420.00')
        self.assertEqual(data['items'][0]['quantity'],'7.000')
        self.assertEqual(data['items'][0]['average'],'60.000000')
        self.execute(sale,'documents','reverse')
        data=self.report('stock-movements').json()
        self.assertEqual(data['total'],3)
        self.assertEqual(data['summary']['value'],'600.00')
        self.assertEqual(data['items'][-1]['action'],'Сторно')
        self.assertEqual(data['items'][-1]['quantity'],'3.000')

    def test_money_ledger_filters_dates_and_retains_original_after_reversal(self):
        account=self.account()
        payment=self.pay(self.payload(account,date='2026-11-14')).json()['payment']
        self.assertEqual(self.command(payment,'reverse').status_code,200)
        before=self.report('payments','?from=2026-11-14&to=2026-11-14').json()
        after=self.report('payments','?from=2026-11-15&to=2026-11-15').json()
        self.assertEqual(before['summary'],{'inflow':'1000.00','outflow':'0.00','net':'1000.00'})
        self.assertEqual(after['summary'],{'inflow':'0.00','outflow':'1000.00','net':'-1000.00'})
        self.assertEqual(after['items'][0]['action'],'Сторно')
        self.assertEqual(self.report('payments').json()['summary']['net'],'0.00')

    def test_debts_use_current_allocations_of_documents_in_selected_period(self):
        sale=self.posted(self.sale(service=True,day='2026-11-14'),'documents')
        payment=self.pay(self.payload(amount='100',allocations=[{'source':'sale','document':sale['id'],'amount':'100'}])).json()['payment']
        data=self.report('debts-sales','?from=2026-11-14&to=2026-11-14').json()
        self.assertEqual(data['summary'],{'total':'300.00','paid':'100.00','due':'200.00'})
        self.assertEqual(data['items'][0]['payment_status'],'Частково')
        self.assertIn('незалежно від дати платежу',data['note'])
        self.command(payment,'reverse')
        self.assertEqual(self.report('debts-sales').json()['summary']['due'],'300.00')

    def test_supplier_debt_excludes_opening_and_drafts(self):
        self.stock()
        receipt=self.posted(self.stock(kind='receipt'))
        account=self.account();self.pay(self.payload(account))
        self.pay(self.payload(account,direction='out',amount='400',allocations=[{'source':'receipt','document':receipt['id'],'amount':'400'}]))
        data=self.report('debts-receipts').json()
        self.assertEqual(data['total'],1)
        self.assertEqual(data['summary'],{'total':'600.00','paid':'400.00','due':'200.00'})

    def test_filters_apply_to_rows_totals_and_csv_identically(self):
        self.posted(self.stock(day='2026-11-14'))
        first=self.posted(self.sale(day='2026-11-14'),'documents')
        self.posted(self.sale(day='2026-11-15'),'documents')
        query=f'?from=2026-11-14&to=2026-11-14&warehouse={self.warehouse.pk}&counterparty={self.customer.pk}'
        data=self.report('sales',query).json()
        response=self.report('sales',query,True)
        self.assertEqual(response.status_code,200,response.content)
        rows=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1][1],first['number'])
        self.assertEqual(rows[1][-1],data['summary']['total'])
        self.assertIn('no-store',response['Cache-Control'])
        self.assertIn('attachment',response['Content-Disposition'])

    def test_csv_protects_formula_text_but_preserves_numeric_negatives(self):
        Counterparty.objects.filter(pk=self.customer.pk).update(name='  =HYPERLINK("https://example.org")')
        self.posted(self.sale(service=True),'documents')
        response=self.report('sales',export=True)
        rows=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
        self.assertTrue(rows[1][2].startswith("'"))
        for value in ['=1+1',' +SUM(A1)','-2+3','@SUM(A1)','\tcontent','\rvalue','\nvalue']:
            self.assertEqual(csv_text(value),"'"+value)
        account=self.account();incoming=self.pay(self.payload(account)).json()['payment'];self.command(incoming,'reverse')
        rows=list(csv.reader(io.StringIO(self.report('payments',export=True).content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(rows[-1][4],'-1000.00')

    def test_pagination_totals_and_export_all_filtered_rows(self):
        documents=[Document(organization=self.org,year=2026,number=i+1,date='2026-11-15',counterparty=self.customer,counterparty_snapshot={'name':'Client'},status='posted',total=100) for i in range(51)]
        Document.objects.bulk_create(documents)
        Posting.objects.bulk_create([Posting(organization=self.org,sale=d,created_by=self.user) for d in documents])
        data=self.report('sales','?page=2').json()
        self.assertEqual(len(data['items']),1)
        self.assertEqual(data['total'],51)
        self.assertEqual(data['summary']['total'],'5100.00')
        rows=list(csv.reader(io.StringIO(self.report('sales','?page=2',True).content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),52)

    def test_financial_permissions_mfa_and_revocation_cover_exports(self):
        for role in ['manager','storekeeper','viewer']:
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            self.assertFalse(self.client.get(self.base+'/report-options').json()['read'])
            for kind in ['sales','margin','balances','stock-movements','payments','debts-sales','debts-receipts']:
                self.assertEqual(self.report(kind).status_code,403)
                self.assertEqual(self.report(kind,export=True).status_code,403)
        Membership.objects.filter(pk=self.member.pk).update(role='accountant')
        self.assertEqual(self.report('sales').status_code,200)
        Membership.objects.filter(pk=self.member.pk).update(is_active=False)
        self.assertEqual(self.report('sales',export=True).status_code,403)
        self.assertEqual(Client().get(self.base+'/reports/sales').status_code,401)

    def test_cross_organization_sources_and_filters_never_leak(self):
        foreign=Counterparty.objects.create(organization=self.other,name='Secret org',kind='company')
        self.assertEqual(self.client.get(f'/api/v1/organizations/{self.other.pk}/reports/sales').status_code,403)
        response=self.report('sales',f'?counterparty={foreign.pk}')
        self.assertEqual(response.status_code,400)
        self.assertNotIn(b'Secret org',response.content)
        Membership.objects.create(user=self.user,organization=self.other,role='owner')
        self.assertEqual(self.client.get(f'/api/v1/organizations/{self.other.pk}/reports/sales').json()['total'],0)

    def test_filter_errors_are_explicit_and_archived_records_can_be_selected(self):
        for kind,query in [('sales','?from=2026-11-16&to=2026-11-15'),('sales','?from=nope'),('sales','?page=0'),('sales','?page=oops'),('sales','?page=1&page=2'),('sales','?account=invalid'),('balances','?from=2026-11-15'),('balances','?warehouse=invalid')]:
            self.assertEqual(self.report(kind,query).status_code,400,(kind,query))
        Counterparty.objects.filter(pk=self.customer.pk).update(is_active=False)
        self.assertEqual(self.report('sales',f'?counterparty={self.customer.pk}').status_code,200)
        self.assertEqual(self.report('unknown').status_code,400)

    def test_export_limit_fails_instead_of_silently_truncating(self):
        query=MagicMock();query.count.return_value=10001
        with patch('reports.services.queryset',return_value=query):
            result=self.report('sales',export=True)
        self.assertEqual(result.status_code,400)
        self.assertIn('10 000',result.json()['error'])
        self.assertFalse(query.__iter__.called)

    def test_empty_report_and_page_work_without_fake_seed_records(self):
        self.assertEqual(self.client.get('/reports/').status_code,200)
        for kind in ['sales','margin','balances','stock-movements','payments','debts-sales','debts-receipts']:
            data=self.report(kind).json()
            self.assertEqual(data['items'],[])
            self.assertEqual(data['total'],0)
            self.assertTrue(all(Decimal(value)==0 for value in data['summary'].values()))
