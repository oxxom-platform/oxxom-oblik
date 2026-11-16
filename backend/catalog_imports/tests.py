import io
import csv
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,Client,TransactionTestCase,skipUnlessDBFeature
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from accounts.models import User,Membership
from catalog.models import Unit,Product,Counterparty,Warehouse,CatalogEvent
from documents.models import Document
from posting.models import Movement
from posting import tests as fixture
from .models import ImportBatch,ImportEvent


class ImportTests(TestCase):
    setUpTestData=classmethod(fixture.PostingTests.setUpTestData.__func__)
    setUp=fixture.PostingTests.setUp
    post=fixture.PostingTests.post

    def upload(self,kind,content):
        content=content.encode('utf-8') if isinstance(content,str) else content
        return self.client.post(self.base+'/imports/'+kind+'/preview',{'file':SimpleUploadedFile('catalog.csv',content,content_type='text/csv')})

    def preview(self,kind,content):
        response=self.upload(kind,content)
        self.assertEqual(response.status_code,201,response.content)
        return response.json()['batch']

    def command(self,batch,action='confirm',**extra):
        return self.post(self.base+'/imports/'+batch['id']+'/'+action,{'version':batch['version'],**extra})

    def test_preview_has_no_catalog_or_ledger_changes_then_explicit_confirm(self):
        before=Warehouse.objects.count()
        batch=self.preview('warehouses','name\nНовий склад\nЩе один склад\n')
        self.assertEqual(batch['counts'],{'new':2,'duplicate':0,'error':0})
        self.assertEqual(Warehouse.objects.count(),before)
        response=self.command(batch)
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['created'],2)
        self.assertEqual(Warehouse.objects.count(),before+2)
        self.assertEqual(CatalogEvent.objects.filter(action='import').count(),2)
        self.assertFalse(Document.objects.exists());self.assertFalse(Movement.objects.exists())

    def test_confirmation_retry_is_exactly_once_and_other_payload_conflicts(self):
        batch=self.preview('warehouses','name\nNew\n')
        first=self.command(batch);second=self.command(batch)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(Warehouse.objects.filter(name='New').count(),1)
        self.assertEqual(ImportEvent.objects.filter(action='confirm').count(),1)
        self.assertEqual(self.command(batch,unexpected=True).status_code,409)
        self.assertEqual(self.command({**batch,'version':2}).status_code,409)

    def test_error_row_blocks_whole_file(self):
        before=Counterparty.objects.count()
        batch=self.preview('counterparties','name;kind;code;email;phone;address;bank_details;note\nValid;company;;;;;;\nInvalid;company;;not-email;;;;\n')
        self.assertEqual(batch['counts']['error'],1)
        self.assertEqual(self.command(batch).status_code,400)
        self.assertEqual(Counterparty.objects.count(),before)
        self.assertFalse(CatalogEvent.objects.exists())

    def test_existing_active_archived_and_case_whitespace_duplicates_are_skipped(self):
        Warehouse.objects.filter(pk=self.warehouse.pk).update(is_active=False)
        batch=self.preview('warehouses','name\n  склад  \nНовий\nНовий\n')
        self.assertEqual(batch['counts'],{'new':1,'duplicate':2,'error':0})
        self.assertFalse(batch['rows'][0]['existing'][0]['is_active'])
        result=self.command(batch).json()
        self.assertEqual(result['created'],1);self.assertEqual(result['skipped'],2)
        self.warehouse.refresh_from_db();self.assertFalse(self.warehouse.is_active)

    def test_conflicting_duplicate_within_file_is_an_error(self):
        batch=self.preview('units','name;symbol\nКілограм;кг\nКілограми;кг\n')
        self.assertEqual(batch['rows'][1]['status'],'error')
        self.assertEqual(self.command(batch).status_code,400)
        self.assertFalse(Unit.objects.filter(symbol='кг').exists())

    def test_unit_matching_is_scoped_active_and_unambiguous(self):
        Unit.objects.create(organization=self.other,name='Foreign',symbol='foreign')
        batch=self.preview('products','name;kind;sku;unit;price\nOne;goods;;foreign;10\n')
        self.assertEqual(batch['counts']['error'],1)
        duplicate=Unit.objects.create(organization=self.org,name='Other unit',symbol=self.unit.symbol)
        batch=self.preview('products',f'name;kind;sku;unit;price\nOne;goods;;{self.unit.symbol};10\n')
        self.assertEqual(batch['counts']['error'],1)
        duplicate.delete();Unit.objects.filter(pk=self.unit.pk).update(is_active=False)
        batch=self.preview('products',f'name;kind;sku;unit;price\nOne;goods;;{self.unit.symbol};10\n')
        self.assertEqual(batch['counts']['error'],1)

    def test_products_services_decimal_comma_and_leading_zero_sku(self):
        content=f'\ufeffname;kind;sku;unit;price\nНовий товар;goods;0001;{self.unit.symbol};1500,25\nНова послуга;service;0002;{self.unit.symbol};0.00\n'
        batch=self.preview('products',content)
        self.assertEqual(batch['counts']['new'],2)
        result=self.command(batch);self.assertEqual(result.status_code,200,result.content)
        item=Product.objects.get(sku='0001');self.assertEqual(str(item.price),'1500.25')
        self.assertEqual(item.unit_id,self.unit.pk)
        self.assertEqual(Product.objects.get(sku='0002').kind,'service')

    def test_existing_price_is_never_overwritten(self):
        Product.objects.filter(pk=self.goods.pk).update(sku='0001')
        batch=self.preview('products',f'name;kind;sku;unit;price\nChanged;goods;0001;{self.unit.symbol};999\nFresh;goods;NEW;{self.unit.symbol};100\n')
        self.assertEqual(batch['counts']['duplicate'],1)
        self.assertEqual(self.command(batch).status_code,200)
        self.goods.refresh_from_db();self.assertEqual(self.goods.name,'Товар');self.assertEqual(self.goods.price,100)

    def test_reference_change_requires_refresh_and_new_review_version(self):
        batch=self.preview('products',f'name;kind;sku;unit;price\nFresh;goods;NEW;{self.unit.symbol};100\n')
        Unit.objects.filter(pk=self.unit.pk).update(name='New name',version=2)
        self.assertEqual(self.command(batch).status_code,409)
        self.assertFalse(Product.objects.filter(sku='NEW').exists())
        refreshed=self.command(batch,'refresh').json()['batch']
        self.assertEqual(refreshed['version'],2)
        self.assertEqual(self.command(batch).status_code,409)
        self.assertEqual(self.command(refreshed).status_code,200)

    def test_new_catalog_duplicate_after_preview_does_not_silently_skip(self):
        batch=self.preview('warehouses','name\nNew\nOther\n')
        Warehouse.objects.create(organization=self.org,name='New')
        self.assertEqual(self.command(batch).status_code,409)
        self.assertFalse(Warehouse.objects.filter(name='Other').exists())
        refreshed=self.command(batch,'refresh').json()['batch']
        self.assertEqual(refreshed['counts']['duplicate'],1)
        self.assertEqual(self.command(refreshed).json()['created'],1)

    def test_audit_failure_rolls_back_entire_confirmation(self):
        batch=self.preview('warehouses','name\nOne\nTwo\n')
        with patch('catalog_imports.services.CatalogEvent.objects.create',side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):self.command(batch)
        self.assertFalse(Warehouse.objects.filter(name__in=['One','Two']).exists())
        saved=ImportBatch.objects.get(pk=batch['id']);self.assertEqual(saved.status,'preview')
        self.assertFalse(ImportEvent.objects.filter(action='confirm').exists())
        self.assertEqual(self.command(batch).status_code,200)

    def test_expiry_and_refresh(self):
        batch=self.preview('warehouses','name\nNew\n')
        ImportBatch.objects.filter(pk=batch['id']).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.command(batch).status_code,409)
        refreshed=self.command(batch,'refresh').json()['batch']
        self.assertFalse(refreshed['expired'])
        self.assertEqual(self.command(refreshed).status_code,200)

    def test_roles_templates_mfa_and_csrf(self):
        for role,allowed in [('manager',{'counterparties'}),('storekeeper',{'warehouses'}),('viewer',set()),('accountant',{'units','products','warehouses','counterparties'})]:
            Membership.objects.filter(pk=self.member.pk).update(role=role)
            self.assertEqual({x['kind'] for x in self.client.get(self.base+'/import-options').json()['kinds']},allowed)
            for kind in ['units','products','warehouses','counterparties']:
                self.assertEqual(self.client.get(self.base+'/import-templates/'+kind).status_code,200 if kind in allowed else 403)
            if 'warehouses' not in allowed:self.assertEqual(self.upload('warehouses','name\nNew\n').status_code,403)
        Membership.objects.filter(pk=self.member.pk).update(role='owner')
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        session=client.session;session['mfa_started']=session['mfa_last']=__import__('time').time();session.save()
        self.assertEqual(client.post(self.base+'/imports/warehouses/preview',{'file':SimpleUploadedFile('a.csv',b'name\nNew\n')}).status_code,403)
        self.assertEqual(Client().get(self.base+'/import-options').status_code,401)

    def test_batches_belong_to_creator_and_organization_and_revocation_applies(self):
        batch=self.preview('warehouses','name\nNew\n')
        self.assertEqual(self.client.get(f'/api/v1/organizations/{self.other.pk}/imports/{batch["id"]}').status_code,404)
        actor=User.objects.create_user('other-importer@example.com','Test-Only-Password-782!')
        Membership.objects.create(organization=self.org,user=actor,role='owner')
        ImportBatch.objects.filter(pk=batch['id']).update(actor=actor)
        self.assertEqual(self.client.get(self.base+'/imports/'+batch['id']).status_code,404)
        self.assertEqual(self.command(batch).status_code,404)
        self.assertEqual(self.client.get(self.base+'/imports').json()['total'],0)
        ImportBatch.objects.filter(pk=batch['id']).update(actor=self.user)
        Membership.objects.filter(pk=self.member.pk).update(is_active=False)
        self.assertEqual(self.command(batch).status_code,403)

    def test_invalid_utf8_header_shape_syntax_and_limits(self):
        for content in [b'\xff\xfea',b'name,name\nA,B',b'name\n',b'name\nA;B',b'name\n"unterminated',b'name\nA\x00B',b'x'*(1024*1024+1)]:
            self.assertEqual(self.upload('warehouses',content).status_code,400,content[:30])
        self.assertEqual(self.upload('warehouses','name\n'+'New\n'*1001).status_code,400)
        self.assertFalse(ImportBatch.objects.exists())

    def test_csv_comma_tab_multiline_and_literal_formula_are_text(self):
        batch=self.preview('units','name,symbol\nKilogram,kg\n')
        self.assertEqual(self.command(batch).status_code,200)
        batch=self.preview('warehouses','name\n"=HYPERLINK(""https://example.org"")"\n')
        self.assertEqual(self.command(batch).status_code,200)
        self.assertTrue(Warehouse.objects.filter(name__startswith='=HYPERLINK').exists())
        batch=self.preview('counterparties','name\tkind\tcode\temail\tphone\taddress\tbank_details\tnote\nLiteral\tcompany\t0000123\t\t\t\t\t"Line one\nLine two"\n')
        self.assertEqual(batch['rows'][0]['line'],2)
        self.assertEqual(self.command(batch).status_code,200)
        self.assertEqual(Counterparty.objects.get(code='0000123').note,'Line one\nLine two')

    def test_zero_new_rows_template_download_and_preview_pagination(self):
        self.assertEqual(self.client.get('/imports/').status_code,200)
        response=self.client.get(self.base+'/import-templates/warehouses')
        self.assertEqual(response.content.decode('utf-8-sig'),'name\r\n')
        self.assertIn('no-store',response['Cache-Control'])
        batch=self.preview('warehouses','name\nСклад\n')
        self.assertEqual(self.command(batch).status_code,409)
        batch=self.preview('warehouses','name\n'+'\n'.join('New '+str(n) for n in range(51)))
        self.assertEqual(len(batch['rows']),50)
        detail=self.client.get(self.base+'/imports/'+batch['id']+'?page=2').json()['batch']
        self.assertEqual(len(detail['rows']),1)
        self.assertEqual(detail['total'],51)
        self.assertEqual(self.command(batch).json()['created'],51)

    def test_invalid_product_prices_do_not_get_coerced_or_imported(self):
        rows=['name;kind;sku;unit;price']+[f'Bad {n};goods;B{n};{self.unit.symbol};{price}' for n,price in enumerate(['-1','1.001','NaN','=1+1','1e3'])]
        batch=self.preview('products','\n'.join(rows))
        self.assertEqual(batch['counts']['error'],5)
        self.assertEqual(self.command(batch).status_code,400)
        self.assertFalse(Product.objects.filter(sku__startswith='B').exists())


class ImportConcurrencyTests(TransactionTestCase):
    setUp=fixture.PostingConcurrencyTests.setUp
    race=fixture.PostingConcurrencyTests.race
    post=ImportTests.post
    upload=ImportTests.upload
    preview=ImportTests.preview

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_confirmations_of_same_batch_create_records_once(self):
        batch=self.preview('warehouses','name\nNew\n')
        request=(self.base+'/imports/'+batch['id']+'/confirm',{'version':batch['version']})
        results=self.race([request,request])
        self.assertEqual([r[0] for r in results],[200,200])
        self.assertEqual(results[0][1],results[1][1])
        self.assertEqual(Warehouse.objects.filter(name='New').count(),1)
        self.assertEqual(CatalogEvent.objects.filter(action='import').count(),1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_competing_batches_cannot_create_same_new_record(self):
        batches=[self.preview('warehouses','name\nNew\n') for _ in range(2)]
        requests=[(self.base+'/imports/'+batch['id']+'/confirm',{'version':batch['version']}) for batch in batches]
        results=self.race(requests)
        self.assertEqual(sorted(r[0] for r in results),[200,409])
        self.assertEqual(Warehouse.objects.filter(name='New').count(),1)
