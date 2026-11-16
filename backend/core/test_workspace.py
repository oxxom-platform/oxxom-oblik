import time
from uuid import uuid4
from django.test import TestCase, Client
from accounts.models import User, Organization, Membership
from catalog.models import Counterparty, Warehouse
from documents.models import Document
from inventory.models import StockDraft
from operations.models import Operation
from payments.models import Payment, MoneyAccount


class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('workspace@example.test', 'Test-only-password-829!')
        cls.org = Organization.objects.create(name='A workspace')
        cls.other = Organization.objects.create(name='B workspace')
        cls.member = Membership.objects.create(user=cls.user, organization=cls.org, role='owner')
        cls.second = Membership.objects.create(user=cls.user, organization=cls.other, role='viewer')
        cls.foreign = Organization.objects.create(name='Foreign secret')
        cls.cp = Counterparty.objects.create(organization=cls.org, name='Client', kind='company')
        cls.wh = Warehouse.objects.create(organization=cls.org, name='Warehouse')
        cls.account = MoneyAccount.objects.create(organization=cls.org, name='Cash', kind='cash')
        for number, status, active in [(1, 'draft', True), (2, 'posted', True), (3, 'draft', False)]:
            Document.objects.create(organization=cls.org, year=2026, number=number, date='2026-11-15',
                counterparty=cls.cp, counterparty_snapshot={}, total='98765.43', status=status, is_active=active)
        StockDraft.objects.create(organization=cls.org, year=2026, number=1, date='2026-11-15', kind='receipt', warehouse=cls.wh)
        Operation.objects.create(organization=cls.org, year=2026, number=1, date='2026-11-15', kind='transfer', warehouse=cls.wh)
        Operation.objects.create(organization=cls.org, year=2026, number=2, date='2026-11-15', kind='return', warehouse=cls.wh)
        Payment.objects.create(organization=cls.org, year=2026, number=1, date='2026-11-15',
            account=cls.account, counterparty=cls.cp, counterparty_snapshot={}, direction='in', amount='123456.78')

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()
        self.base = f'/api/v1/organizations/{self.org.pk}/workspace-overview'

    def role(self, role):
        Membership.objects.filter(pk=self.member.pk).update(role=role)

    def test_dashboard_requires_mfa_and_home_is_public(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        self.client.logout()
        self.assertRedirects(self.client.get('/app/'), '/login/', fetch_redirect_response=False)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.base).status_code, 401)
        self.assertRedirects(self.client.get('/team/'), '/login/', fetch_redirect_response=False)

    def test_active_context_survives_module_navigation_and_revalidates(self):
        response = self.client.get('/catalog/', {'organization': str(self.other.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'/documents/?organization={self.other.pk}')
        self.assertEqual(self.client.session['workspace_organization'], str(self.other.pk))
        response = self.client.get('/app/')
        self.assertContains(response, 'B workspace')
        self.assertEqual(response.context['workspace']['organization'], str(self.other.pk))
        self.second.is_active = False
        self.second.save(update_fields=['is_active'])
        response = self.client.get('/app/')
        self.assertEqual(response.context['workspace']['organization'], str(self.org.pk))

    def test_explicit_unavailable_org_fails_without_switch_or_leak(self):
        self.client.get('/app/', {'organization': str(self.org.pk)})
        for value in [str(self.foreign.pk), str(uuid4()), 'bad-uuid', '']:
            response = self.client.get('/catalog/', {'organization': value})
            self.assertEqual(response.status_code, 403)
            self.assertNotContains(response, 'Foreign secret', status_code=403)
            self.assertEqual(self.client.session['workspace_organization'], str(self.org.pk))
        self.assertEqual(self.client.get(f'/api/v1/organizations/{self.foreign.pk}/workspace-overview').status_code, 403)

    def test_navigation_uses_role_permissions(self):
        expected = {
            'owner': {'home','sales','inventory','operations','stock','payments','reports','catalog','imports','issuer','team'},
            'accountant': {'home','sales','inventory','operations','stock','payments','reports','catalog','imports','issuer'},
            'manager': {'home','sales','inventory','operations','stock','catalog','imports','issuer'},
            'storekeeper': {'home','inventory','operations','stock','catalog','imports'},
            'viewer': {'home','sales','inventory','operations','stock','catalog','issuer'},
        }
        for role, keys in expected.items():
            self.role(role)
            response = self.client.get('/app/', {'organization': str(self.org.pk)})
            self.assertEqual({n['key'] for n in response.context['workspace']['navigation']}, keys)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertContains(response, '20261115-original-aligned')

    def test_dashboard_counts_only_own_active_drafts_with_no_financial_values(self):
        data = self.client.get(self.base).json()
        cards = {c['key']: c for c in data['cards']}
        self.assertEqual({k: v['drafts'] for k,v in cards.items()}, {'sales':1,'inventory':1,'operations':2,'payments':1})
        self.assertTrue(all(c['create_url'].endswith('&new=1') for c in cards.values()))
        self.assertNotIn('98765', str(data))
        self.assertNotIn('123456', str(data))
        self.assertNotIn('counterparty', str(data))
        other = self.client.get(f'/api/v1/organizations/{self.other.pk}/workspace-overview').json()
        self.assertTrue(all(c['drafts']==0 and c['create_url'] is None for c in other['cards']))

    def test_storekeeper_and_viewer_never_receive_finance_or_forbidden_counts(self):
        self.role('storekeeper')
        cards = {c['key']: c for c in self.client.get(self.base).json()['cards']}
        self.assertEqual(set(cards), {'inventory','operations'})
        self.assertEqual(cards['operations']['drafts'], 1)  # no customer returns
        self.role('viewer')
        cards = self.client.get(self.base).json()['cards']
        self.assertNotIn('payments', {c['key'] for c in cards})
        self.assertTrue(all(c['create_url'] is None for c in cards))
        self.member.is_active = False
        self.member.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(self.base).status_code, 403)

    def test_no_memberships_and_all_modules_share_shell(self):
        paths = ['/app/','/team/','/catalog/','/documents/','/inventory/','/operations/','/stock/','/payments/','/reports/','/organization-details/','/imports/']
        for path in paths:
            response = self.client.get(path, {'organization':str(self.org.pk)})
            self.assertEqual(response.status_code, 200, path)
            self.assertContains(response, 'id="shell-navigation"')
            self.assertContains(response, '/assets/workspace/workspace.js')
            self.assertEqual(sum(n['active'] for n in response.context['workspace']['navigation']), 1)
        Membership.objects.filter(user=self.user).update(is_active=False)
        response = self.client.get('/app/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['workspace']['organization'], '')
        self.assertNotIn('workspace_organization', self.client.session)

    def test_assets_are_allowlisted_and_post_is_rejected(self):
        for name, content_type in [('workspace.css','text/css'), ('workspace.js','text/javascript')]:
            response = self.client.get('/assets/workspace/'+name)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response['Content-Type'].startswith(content_type))
            self.assertGreater(len(b"".join(response.streaming_content)), 20)
        self.assertEqual(self.client.get('/assets/workspace/settings.py').status_code, 404)
        self.assertEqual(self.client.post(self.base).status_code, 405)
