from uuid import uuid4
from django.conf import settings
from django.db import models
from django.utils import timezone


class Balance(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    warehouse = models.ForeignKey('catalog.Warehouse', on_delete=models.PROTECT)
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    unit = models.ForeignKey('catalog.Unit', on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    value = models.DecimalField(max_digits=25, decimal_places=2, default=0)
    last_date = models.DateField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization','warehouse','product'], name='balance_org_warehouse_product'), models.CheckConstraint(condition=models.Q(quantity__gte=0), name='balance_nonnegative_quantity'), models.CheckConstraint(condition=models.Q(value__gte=0), name='balance_nonnegative_value'), models.CheckConstraint(condition=models.Q(quantity__gt=0)|models.Q(value=0), name='empty_balance_zero_value')]


class Posting(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    sale = models.OneToOneField('documents.Document', null=True, on_delete=models.PROTECT)
    stock = models.OneToOneField('inventory.StockDraft', null=True, on_delete=models.PROTECT)
    operation = models.OneToOneField('operations.Operation', null=True, on_delete=models.PROTECT)
    cost_total = models.DecimalField(max_digits=25, decimal_places=2, default=0)
    is_reversed = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    reversed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(sale__isnull=False,stock__isnull=True,operation__isnull=True)|models.Q(sale__isnull=True,stock__isnull=False,operation__isnull=True)|models.Q(sale__isnull=True,stock__isnull=True,operation__isnull=False)), name='posting_one_document')]


class Movement(models.Model):
    posting = models.ForeignKey(Posting, on_delete=models.PROTECT, related_name='movements')
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    warehouse = models.ForeignKey('catalog.Warehouse', on_delete=models.PROTECT)
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    line_id = models.UUIDField()
    product_name = models.CharField(max_length=200)
    unit_symbol = models.CharField(max_length=20)
    warehouse_name = models.CharField(max_length=200)
    date = models.DateField()
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    value = models.DecimalField(max_digits=25, decimal_places=2)
    is_reversal = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['posting','line_id','warehouse','is_reversal'], name='movement_line_warehouse_once')]
        indexes = [models.Index(fields=['organization','warehouse','product','id'], name='movement_stock_order')]


class RequestRecord(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    key = models.UUIDField()
    digest = models.CharField(max_length=64)
    response = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization','key'], name='posting_idempotency_key')]


class Period(models.Model):
    organization = models.OneToOneField('accounts.Organization', primary_key=True, on_delete=models.PROTECT)
    closed_through = models.DateField(null=True)
    version = models.PositiveIntegerField(default=1)


class PeriodEvent(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    old_date = models.DateField(null=True)
    new_date = models.DateField(null=True)
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(default=timezone.now)
