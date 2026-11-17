from uuid import uuid4

from django.db import models
from django.utils import timezone


class DemoRequest(models.Model):
    """This release stores synthetic fixtures only, never visitor contacts."""
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    product = models.CharField(max_length=20, default='oxxom-oblik', editable=False)
    is_test = models.BooleanField(default=True, editable=False)
    language = models.CharField(max_length=2)
    source = models.CharField(max_length=20, default='/demo/', editable=False)
    status = models.CharField(max_length=12, default='new', editable=False)
    payload = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(is_test=True), name='demo_fixture_only'),
            models.CheckConstraint(condition=models.Q(product='oxxom-oblik'), name='demo_product_oblik'),
            models.CheckConstraint(condition=models.Q(language__in=['uk', 'en', 'ru', 'cs']), name='demo_supported_language'),
        ]


class DemoMail(models.Model):
    request = models.OneToOneField(DemoRequest, primary_key=True, on_delete=models.CASCADE)
    state = models.CharField(max_length=16, default='queued', db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt = models.DateTimeField(default=timezone.now, db_index=True)
    lease_until = models.DateTimeField(null=True)
    sent_at = models.DateTimeField(null=True)
    error_code = models.CharField(max_length=60, blank=True)


class DemoThrottle(models.Model):
    # One global test-mode bucket: no visitor IP/email/UA is stored or hashed.
    key = models.CharField(max_length=40, primary_key=True)
    started = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveIntegerField(default=0)
