from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from .models import User

class UserTests(TestCase):
    def test_email_login_and_password_hash(self):
        user = User.objects.create_user("Owner@Example.COM", "test-only-long-password")
        user.refresh_from_db()
        self.assertEqual(user.email, "owner@example.com")
        self.assertNotEqual(user.password, "test-only-long-password")
        self.assertTrue(user.check_password("test-only-long-password"))

    def test_case_insensitive_uniqueness_at_database_level(self):
        User.objects.create_user("owner@example.com", "test-password")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.bulk_create([User(email="OWNER@EXAMPLE.COM", password="!")])

    def test_invalid_email_rejected(self):
        with self.assertRaises(ValidationError):
            User.objects.create_user("bad-email", "test-password")
