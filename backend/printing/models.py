from django.conf import settings
from django.db import models
from django.utils import timezone


class IssuerProfile(models.Model):
    organization = models.OneToOneField('accounts.Organization', primary_key=True, on_delete=models.PROTECT)
    legal_name = models.CharField(max_length=200)
    kind = models.CharField(max_length=16, choices=[('company','Юридична особа'),('sole_trader','ФОП'),('person','Фізична особа')])
    code = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=500, blank=True)
    bank_details = models.CharField(max_length=1000, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    signatory = models.CharField(max_length=200, blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(default=timezone.now)


class ProfileEvent(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)


class PrintEvent(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    document = models.ForeignKey('documents.Document', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=16)
    document_version = models.PositiveIntegerField()
    issuer_source = models.CharField(max_length=16)
    created_at = models.DateTimeField(default=timezone.now)
