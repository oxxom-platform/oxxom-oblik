from uuid import uuid4
from django.conf import settings
from django.db import models
from django.utils import timezone
from catalog.models import Record


class MoneyAccount(Record):
    kind = models.CharField(max_length=8, choices=[('cash', 'Каса'), ('bank', 'Банківський рахунок')])
    details = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(kind__in=['cash', 'bank']), name='money_account_kind')]


class Payment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    year = models.PositiveIntegerField()
    number = models.PositiveIntegerField()
    direction = models.CharField(max_length=8, choices=[('in', 'Надходження'), ('out', 'Виплата')])
    date = models.DateField()
    account = models.ForeignKey(MoneyAccount, on_delete=models.PROTECT)
    account_name = models.CharField(max_length=200)
    account_details = models.CharField(max_length=200, blank=True)
    counterparty = models.ForeignKey('catalog.Counterparty', on_delete=models.PROTECT)
    counterparty_snapshot = models.JSONField()
    purpose = models.CharField(max_length=2000)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    status = models.CharField(max_length=16, default='draft', editable=False)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'year', 'number'], name='payment_org_year_number'), models.CheckConstraint(condition=models.Q(amount__gt=0), name='payment_positive_amount'), models.CheckConstraint(condition=models.Q(direction__in=['in','out']), name='payment_direction')]

    @property
    def display_number(self):
        return f'ОП-{self.year}-{self.number:06d}'


class PaymentSequence(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    year = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'year'], name='payment_sequence_org_year')]


class Allocation(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='allocations')
    sale = models.ForeignKey('documents.Document', null=True, on_delete=models.PROTECT)
    receipt = models.ForeignKey('inventory.StockDraft', null=True, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=20, decimal_places=2)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(sale__isnull=False,receipt__isnull=True)|models.Q(sale__isnull=True,receipt__isnull=False), name='allocation_one_source'), models.CheckConstraint(condition=models.Q(amount__gt=0), name='allocation_positive_amount'), models.UniqueConstraint(fields=['payment','sale'], name='allocation_payment_sale'), models.UniqueConstraint(fields=['payment','receipt'], name='allocation_payment_receipt')]


class MoneyMovement(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name='movements')
    account = models.ForeignKey(MoneyAccount, on_delete=models.PROTECT)
    account_name = models.CharField(max_length=200)
    date = models.DateField()
    amount = models.DecimalField(max_digits=25, decimal_places=2)
    is_reversal = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['payment','is_reversal'], name='money_movement_once')]
        indexes = [models.Index(fields=['organization','account','id'], name='money_account_order')]


class PaymentEvent(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    object_id = models.UUIDField()
    action = models.CharField(max_length=24)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
