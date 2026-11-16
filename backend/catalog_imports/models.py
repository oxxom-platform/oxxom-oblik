from uuid import uuid4
from datetime import timedelta
from django.conf import settings
from django.db import models
from django.utils import timezone


def preview_expiry():
    return timezone.now()+timedelta(hours=24)


class ImportBatch(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid4,editable=False)
    organization=models.ForeignKey('accounts.Organization',on_delete=models.PROTECT)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    kind=models.CharField(max_length=20)
    filename=models.CharField(max_length=200)
    file_digest=models.CharField(max_length=64)
    inputs=models.JSONField()
    plan=models.JSONField()
    counts=models.JSONField(default=dict)
    plan_digest=models.CharField(max_length=64)
    status=models.CharField(max_length=12,default='preview')
    version=models.PositiveIntegerField(default=1)
    result=models.JSONField(default=dict)
    created_at=models.DateTimeField(default=timezone.now)
    expires_at=models.DateTimeField(default=preview_expiry)
    applied_at=models.DateTimeField(null=True)

    class Meta:
        indexes=[models.Index(fields=['organization','actor','created_at'],name='import_actor_batches')]


class ImportEvent(models.Model):
    batch=models.ForeignKey(ImportBatch,on_delete=models.PROTECT)
    organization=models.ForeignKey('accounts.Organization',on_delete=models.PROTECT)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    action=models.CharField(max_length=16)
    details=models.JSONField(default=dict)
    created_at=models.DateTimeField(default=timezone.now)
