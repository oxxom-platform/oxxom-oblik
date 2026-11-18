"""Negative API sweep across the actual URL registry, using synthetic tenants.

This complements (not replaces) each module's valid-object/field/role tests.
No production host, data, user, MFA device or credentials are used.
"""
import json
import re
import time
from uuid import uuid4

from django.apps import apps
from django.test import Client, TestCase
from django.urls import get_resolver

from accounts.models import Membership, Organization, User


def tenant_routes(organization_id):
    for pattern in get_resolver().url_patterns:
        route = str(pattern.pattern)
        if not route.startswith('api/v1/organizations/<uuid:organization_id>/'):
            continue
        path = route.replace('<uuid:organization_id>', str(organization_id))
        path = re.sub(r'<uuid:[^>]+>', str(uuid4()), path)
        path = re.sub(r'<str:[^>]+>', 'products', path)
        if '<' in path:
            raise AssertionError('New converter requires an explicit audit case')
        yield '/' + path


class ApiBoundaryMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('matrix@example.test', 'Synthetic-Matrix-Only-342!')
        cls.org = Organization.objects.create(name='Synthetic tenant A')
        cls.other = Organization.objects.create(name='Synthetic tenant B')
        cls.member = Membership.objects.create(user=cls.user, organization=cls.org, role='owner')

    def login(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()

    def snapshot(self):
        # Exclude only Django sessions: protected requests update MFA bookkeeping.
        return {model._meta.label: json.dumps(list(model.objects.order_by('pk').values()),
                                              sort_keys=True, default=str)
                for model in apps.get_models() if model._meta.app_label not in
                {'sessions', 'contenttypes', 'auth'}}

    def assert_denied(self, organization_id, anonymous=False):
        before = self.snapshot()
        for path in tenant_routes(organization_id):
            for method in ('GET', 'POST'):
                with self.subTest(path=path, method=method):
                    if method == 'GET':
                        response = self.client.get(path)
                    elif '/members/' in path:
                        response = self.client.post(path, {
                            'email': 'new-synthetic@example.test', 'role': 'viewer',
                            'password': 'Synthetic-New-Account-893!', 'is_active': 'false',
                        })
                    else:
                        response = self.client.post(path, '{}', content_type='application/json')
                    if anonymous:
                        self.assertIn(response.status_code, (401, 405))
                    elif response.status_code == 200:
                        # These intentionally return public capability metadata,
                        # never a successful record read or mutation.
                        endpoint = path.rsplit('/', 1)[-1]
                        self.assertEqual(method, 'GET')
                        self.assertIn(endpoint, {'document-permissions', 'inventory-permissions',
                            'operation-permissions', 'payment-permissions', 'report-options',
                            'import-options'})
                        data = response.json()
                        if endpoint == 'import-options':
                            self.assertEqual(data, {'kinds': [], 'max_bytes': 1048576, 'max_rows': 1000})
                        else:
                            self.assertTrue(all(value in (False, [], None) for value in data.values()))
                    else:
                        self.assertIn(response.status_code, (403, 404, 405))
        self.assertEqual(before, self.snapshot(), 'Denied request changed application records')

    def test_every_tenant_api_requires_verified_mfa(self):
        self.assert_denied(self.org.pk, anonymous=True)
        self.client.force_login(self.user)
        self.assert_denied(self.org.pk, anonymous=True)

    def test_five_roles_cannot_access_foreign_tenant(self):
        for role in Membership.Role.values:
            with self.subTest(role=role):
                Membership.objects.filter(pk=self.member.pk).update(role=role)
                self.login()
                self.assert_denied(self.other.pk)

    def test_revocation_inactive_org_and_superuser_do_not_bypass_scope(self):
        self.login()
        Membership.objects.filter(pk=self.member.pk).update(is_active=False)
        self.assert_denied(self.org.pk)
        Membership.objects.filter(pk=self.member.pk).update(is_active=True)
        Organization.objects.filter(pk=self.org.pk).update(is_active=False)
        self.assert_denied(self.org.pk)
        self.user.is_superuser = True
        self.user.save()
        self.login()
        self.assert_denied(self.other.pk)

    def test_logout_invalidates_replayed_session_cookie(self):
        self.login()
        previous = self.client.cookies['sessionid'].value
        self.assertEqual(self.client.get('/api/v1/organizations').status_code, 200)
        self.assertEqual(self.client.post('/api/v1/auth/logout').status_code, 200)
        replay = Client()
        replay.cookies['sessionid'] = previous
        self.assertEqual(replay.get('/api/v1/organizations').status_code, 401)
