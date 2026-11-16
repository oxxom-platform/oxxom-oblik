import time
from unittest.mock import patch
from django.test import TestCase, Client
from django.db import IntegrityError
from .models import User, Organization, Membership, AccessEvent


class MemberManagementTests(TestCase):
    password = 'Testing-only-Password-782!'

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user('owner@example.com', cls.password)
        cls.org = Organization.objects.create(name='A')
        cls.other = Organization.objects.create(name='B')
        cls.ownership = Membership.objects.create(user=cls.owner, organization=cls.org, role='owner')
        cls.worker = User.objects.create_user('worker@example.com', cls.password)
        cls.worker_member = Membership.objects.create(user=cls.worker, organization=cls.org, role='viewer')
        cls.foreign = Membership.objects.create(user=cls.worker, organization=cls.other, role='owner')

    def authenticated(self, user=None, client=None):
        client = client or self.client
        client.force_login(user or self.owner)
        session = client.session
        session['mfa_started'] = session['mfa_last'] = time.time()
        session.save()
        return client

    def setUp(self):
        self.authenticated()
        self.base = f'/api/v1/organizations/{self.org.pk}'
        self.foreign_base = f'/api/v1/organizations/{self.other.pk}'

    def test_password_without_mfa_cannot_manage_members(self):
        self.client.logout()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.base + '/members').status_code, 401)
        self.assertEqual(self.client.post(self.base + '/members/add', {}).status_code, 401)
        self.assertEqual(self.client.get('/app/').status_code, 302)

    def test_workspace_and_member_listing(self):
        response = self.client.get('/app/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        response = self.client.get(self.base + '/members')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 2)
        self.assertNotContains(response, self.password)

    def test_create_user_membership_and_audit_atomically(self):
        response = self.client.post(self.base + '/members/add', {'email': 'NEW@example.com', 'name': 'New Employee', 'role': 'manager', 'password': self.password})
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email='new@example.com')
        self.assertTrue(user.check_password(self.password))
        self.assertFalse(user.is_superuser or user.is_staff)
        self.assertEqual(Membership.objects.get(user=user).role, 'manager')
        event = AccessEvent.objects.get()
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.organization, self.org)
        self.assertNotIn(self.password, str(event.details))
        self.assertNotIn('password', response.json()['member'])

    def test_existing_user_password_and_name_not_overwritten(self):
        response = self.client.post(self.base + '/members/add', {'email': 'new-existing@example.com', 'role': 'viewer', 'password': self.password})
        self.assertEqual(response.status_code, 201)
        existing = User.objects.get(email='new-existing@example.com')
        response = self.client.post(self.base + '/members/add', {'email': existing.email, 'role': 'owner'})
        self.assertEqual(response.status_code, 409)
        # A separate organization can grant access but cannot replace global credentials.
        Membership.objects.create(user=self.owner, organization=self.other, role='owner')
        response = self.client.post(self.foreign_base + '/members/add', {'email': existing.email, 'role': 'viewer', 'name': 'Overwrite', 'password': 'Different-Password-For-Test-892!'})
        self.assertEqual(response.status_code, 201)
        existing.refresh_from_db()
        self.assertTrue(existing.check_password(self.password))
        self.assertEqual(existing.display_name, '')

    def test_invalid_password_or_role_leaves_no_partial_user(self):
        for role, password in [('root', self.password), ('viewer', '123')]:
            response = self.client.post(self.base + '/members/add', {'email': 'invalid@example.com', 'role': role, 'password': password})
            self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email='invalid@example.com').exists())
        self.assertFalse(AccessEvent.objects.exists())

    def test_failed_audit_rolls_back_creation(self):
        with patch('accounts.member_views.AccessEvent.objects.create', side_effect=IntegrityError):
            response = self.client.post(self.base + '/members/add', {'email': 'rollback@example.com', 'role': 'viewer', 'password': self.password})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(User.objects.filter(email='rollback@example.com').exists())

    def test_nonowners_cannot_list_add_update_or_read_audit(self):
        for role in ['accountant', 'manager', 'storekeeper', 'viewer']:
            Membership.objects.filter(pk=self.worker_member.pk).update(role=role)
            self.authenticated(self.worker)
            for url in [self.base + '/members', self.base + '/access-events']:
                self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(self.base + '/members/add', {'email': 'x@example.com', 'role': 'viewer', 'password': self.password}).status_code, 403)
            self.assertEqual(self.client.post(self.base + '/members/' + str(self.ownership.pk), {'role': 'viewer', 'is_active': 'false'}).status_code, 403)
        self.assertFalse(User.objects.filter(email='x@example.com').exists())

    def test_known_foreign_uuid_does_not_allow_access_even_as_superuser(self):
        self.owner.is_superuser = True
        self.owner.save()
        self.assertEqual(self.client.get(self.foreign_base + '/members').status_code, 403)
        self.assertEqual(self.client.get(self.foreign_base + '/access-events').status_code, 403)
        self.assertEqual(self.client.post(self.foreign_base + '/members/add', {'email': 'x@example.com', 'role': 'viewer', 'password': self.password}).status_code, 403)
        response = self.client.post(self.base + '/members/' + str(self.foreign.pk), {'role': 'viewer', 'is_active': 'false'})
        self.assertEqual(response.status_code, 404)
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.role, 'owner')

    def test_last_owner_cannot_be_demoted_or_revoked(self):
        for role, active in [('viewer', 'true'), ('owner', 'false')]:
            response = self.client.post(self.base + '/members/' + str(self.ownership.pk), {'role': role, 'is_active': active})
            self.assertEqual(response.status_code, 409)
        self.ownership.refresh_from_db()
        self.assertTrue(self.ownership.is_active)
        self.assertEqual(self.ownership.role, 'owner')

    def test_second_owner_allows_self_demotion_and_immediate_loss_of_manage_access(self):
        Membership.objects.filter(pk=self.worker_member.pk).update(role='owner')
        response = self.client.post(self.base + '/members/' + str(self.ownership.pk), {'role': 'viewer', 'is_active': 'true'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(self.base + '/members').status_code, 403)

    def test_revocation_is_org_scoped_and_prevents_access_immediately(self):
        worker_client = self.authenticated(self.worker, Client())
        response = self.client.post(self.base + '/members/' + str(self.worker_member.pk), {'role': 'viewer', 'is_active': 'false'})
        self.assertEqual(response.status_code, 200)
        orgs = worker_client.get('/api/v1/organizations').json()['organizations']
        self.assertEqual([o['id'] for o in orgs], [str(self.other.pk)])
        self.assertEqual(worker_client.get(self.base + '/members').status_code, 403)
        self.assertTrue(User.objects.get(pk=self.worker.pk).is_active)
        event = AccessEvent.objects.get()
        self.assertEqual(event.details['before']['is_active'], True)
        self.assertEqual(event.details['after']['is_active'], False)

    def test_inactive_org_and_revoked_actor_cannot_manage(self):
        Membership.objects.filter(pk=self.ownership.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.base + '/members').status_code, 403)
        Membership.objects.filter(pk=self.ownership.pk).update(is_active=True)
        Organization.objects.filter(pk=self.org.pk).update(is_active=False)
        self.assertEqual(self.client.post(self.base + '/members/' + str(self.worker_member.pk), {'role': 'manager', 'is_active': 'true'}).status_code, 403)

    def test_member_mutations_require_csrf(self):
        client = self.authenticated(client=Client(enforce_csrf_checks=True))
        self.assertEqual(client.post(self.base + '/members/add', {}).status_code, 403)
        self.assertEqual(client.post(self.base + '/members/' + str(self.worker_member.pk), {}).status_code, 403)

    def test_member_list_is_paginated(self):
        response = self.client.get(self.base + '/members?page=2').json()
        self.assertEqual(response['members'], [])
        self.assertEqual(response['total'], 2)
        self.assertEqual(self.client.get(self.base + '/members?page=invalid').status_code, 200)

    def test_new_account_password_errors_identify_field_and_leave_no_partial_account(self):
        response = self.client.post(self.base + '/members/add', {
            'email': 'password-feedback@example.test', 'name': 'Test', 'role': 'viewer', 'password': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()['fields']['password'])
        self.assertFalse(User.objects.filter(email='password-feedback@example.test').exists())
        self.assertFalse(AccessEvent.objects.exists())

    def test_employee_form_shows_result_next_to_submit_and_retains_existing_account_support(self):
        response = self.client.get('/team/')
        self.assertContains(response, 'id="member-feedback"')
        self.assertContains(response, 'aria-describedby="password-help password-error"')
        self.assertContains(response, 'Для наявного акаунта залиште порожнім.')


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature


class ConcurrentOwnerChangesTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_owners_cannot_simultaneously_revoke_each_other(self):
        org = Organization.objects.create(name='Concurrent owners')
        owners = [User.objects.create_user(f'owner{i}@example.com', 'Testing-only-Password-782!') for i in range(2)]
        memberships = [Membership.objects.create(user=user, organization=org, role='owner') for user in owners]
        clients = [Client(), Client()]
        for client, user in zip(clients, owners):
            client.force_login(user)
            session = client.session
            session['mfa_started'] = session['mfa_last'] = time.time()
            session.save()
        barrier = Barrier(2)

        def revoke(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                target = memberships[1-index]
                return clients[index].post(f'/api/v1/organizations/{org.pk}/members/{target.pk}', {'role': 'owner', 'is_active': 'false'}).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(revoke, range(2)))
        self.assertEqual(sorted(statuses), [200, 403])
        self.assertEqual(Membership.objects.filter(organization=org, role='owner', is_active=True).count(), 1)
        self.assertEqual(AccessEvent.objects.count(), 1)
