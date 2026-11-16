from unittest.mock import patch
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase

class HealthTests(SimpleTestCase):
    def test_health_works_without_database(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIn("no-store", response["Cache-Control"])

    def test_database_failure_is_not_ready_without_secret_leak(self):
        # Leave Django's real connection and SimpleTestCase guards intact.
        with patch("core.views.connection") as database:
            database.cursor.side_effect = OperationalError("password=secret")
            response = self.client.get("/api/v1/ready")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"secret", response.content)

    def test_no_business_or_admin_routes_before_mfa(self):
        for url in ("/api/v1/documents", "/api/sales", "/admin/"):
            self.assertEqual(self.client.get(url).status_code, 404)

class PostgresReadinessTests(TestCase):
    def test_ready_after_migration(self):
        self.assertEqual(self.client.get("/api/v1/ready").status_code, 200)
