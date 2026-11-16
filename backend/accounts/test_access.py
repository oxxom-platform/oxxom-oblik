from io import StringIO
from unittest.mock import patch
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from .access import memberships_for, require_permission
from .bootstrap import create_owner
from .models import Membership, Organization, User


class AccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("owner@example.com", "Test-Only-Strong-Password-974!")
        cls.organization = Organization.objects.create(name="A")
        cls.other = Organization.objects.create(name="B")
        cls.membership = Membership.objects.create(user=cls.user, organization=cls.organization, role=Membership.Role.OWNER)

    def test_owner_is_limited_to_own_organization(self):
        require_permission(self.user, self.organization.pk, "members.manage")
        with self.assertRaises(PermissionDenied):
            require_permission(self.user, self.other.pk, "members.manage")
        self.assertEqual(list(memberships_for(self.user).values_list("organization_id", flat=True)), [self.organization.pk])

    def test_superuser_does_not_bypass_organization(self):
        self.user.is_superuser = True
        self.user.save()
        with self.assertRaises(PermissionDenied):
            require_permission(self.user, self.other.pk, "records.read")

    def test_roles_allow_expected_action_and_deny_privileged_action(self):
        cases = [
            (Membership.Role.ACCOUNTANT, "documents.post", "members.manage"),
            (Membership.Role.MANAGER, "sales.draft", "documents.post"),
            (Membership.Role.STOREKEEPER, "stock.draft", "payments.manage"),
            (Membership.Role.VIEWER, "records.read", "sales.draft"),
        ]
        for role, allowed, denied in cases:
            with self.subTest(role=role):
                self.membership.role = role
                self.membership.save()
                require_permission(self.user, self.organization.pk, allowed)
                with self.assertRaises(PermissionDenied):
                    require_permission(self.user, self.organization.pk, denied)

    def test_unknown_action_denied_even_for_owner(self):
        with self.assertRaises(PermissionDenied):
            require_permission(self.user, self.organization.pk, "unknown")

    def test_revoked_membership_denies_access(self):
        Membership.objects.filter(pk=self.membership.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            require_permission(self.user, self.organization.pk, "records.read")

    def test_inactive_organization_denies_access(self):
        Organization.objects.filter(pk=self.organization.pk).update(is_active=False)
        self.assertFalse(memberships_for(self.user).exists())

    def test_inactive_and_anonymous_users_have_no_access(self):
        self.user.is_active = False
        self.assertFalse(memberships_for(self.user).exists())
        self.assertFalse(memberships_for(AnonymousUser()).exists())

    def test_duplicate_membership_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(user=self.user, organization=self.organization, role=Membership.Role.VIEWER)

    def test_invalid_role_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(user=self.user, organization=self.other, role="invalid")


class OwnerProvisioningTests(TestCase):
    password = "Test-Only-Strong-Password-974!"

    def test_owner_is_not_global_admin(self):
        user, organization = create_owner(email="OWNER@example.com", password=self.password, organization_name=" Test ")
        self.assertEqual(organization.name, "Test")
        self.assertEqual(user.email, "owner@example.com")
        self.assertTrue(user.check_password(self.password))
        self.assertFalse(user.is_staff or user.is_superuser)
        require_permission(user, organization.pk, "members.manage")

    def test_invalid_input_does_not_create_partial_records(self):
        for email, password, name in [("bad-email", self.password, "Test"), ("a@example.com", "123", "Test"), ("a@example.com", self.password, " ")]:
            with self.subTest(email=email, name=name), self.assertRaises(ValidationError):
                create_owner(email=email, password=password, organization_name=name)
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Organization.objects.count(), 0)

    def test_existing_account_is_never_promoted_or_password_replaced(self):
        user = User.objects.create_user("owner@example.com", self.password)
        with self.assertRaises(ValidationError):
            create_owner(email=user.email, password="Different-Test-Password-783!", organization_name="Test")
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.password))
        self.assertFalse(Membership.objects.exists())
        self.assertFalse(Organization.objects.exists())

    def test_failure_rolls_back_user_and_organization(self):
        with patch("accounts.bootstrap.Membership.objects.create", side_effect=IntegrityError):
            with self.assertRaises(IntegrityError):
                create_owner(email="owner@example.com", password=self.password, organization_name="Test")
        self.assertFalse(User.objects.exists())
        self.assertFalse(Organization.objects.exists())

    def test_command_rejects_mismatched_passwords(self):
        with patch("accounts.management.commands.create_owner.sys.stdin.isatty", return_value=True), patch("accounts.management.commands.create_owner.getpass.getpass", side_effect=[self.password, "different"]):
            with self.assertRaises(CommandError):
                call_command("create_owner", email="owner@example.com", organization="Test", stdout=StringIO())
        self.assertFalse(User.objects.exists())

    def test_command_output_does_not_expose_password(self):
        output = StringIO()
        with patch("accounts.management.commands.create_owner.sys.stdin.isatty", return_value=True), patch("accounts.management.commands.create_owner.getpass.getpass", return_value=self.password):
            call_command("create_owner", email="owner@example.com", organization="Test", stdout=output)
        self.assertNotIn(self.password, output.getvalue())
        self.assertEqual(Membership.objects.get().role, Membership.Role.OWNER)
