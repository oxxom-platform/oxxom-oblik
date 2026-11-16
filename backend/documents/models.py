from uuid import uuid4
from django.conf import settings
from django.db import models
from django.utils import timezone


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    year = models.PositiveIntegerField()
    number = models.PositiveIntegerField()
    date = models.DateField()
    counterparty = models.ForeignKey('catalog.Counterparty', on_delete=models.PROTECT)
    counterparty_snapshot = models.JSONField()
    issuer_snapshot = models.JSONField(default=dict, blank=True)
    warehouse = models.ForeignKey('catalog.Warehouse', null=True, blank=True, on_delete=models.PROTECT)
    warehouse_name = models.CharField(max_length=200, blank=True)
    note = models.CharField(max_length=2000, blank=True)
    status = models.CharField(max_length=16, default='draft', editable=False)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    total = models.DecimalField(max_digits=25, decimal_places=2)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'year', 'number'], name='sale_number_per_org_year'), models.CheckConstraint(condition=models.Q(total__gte=0), name='sale_total_nonnegative')]

    @property
    def display_number(self):
        return f'РЛ-{self.year}-{self.number:06d}'


class DocumentSequence(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    year = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'year'], name='sale_sequence_per_org_year')]


class DocumentLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='lines')
    position = models.PositiveSmallIntegerField()
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    product_name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10)
    unit_name = models.CharField(max_length=200)
    unit_symbol = models.CharField(max_length=20)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    amount = models.DecimalField(max_digits=23, decimal_places=2)

    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['document', 'position'], name='sale_line_position_unique'), models.CheckConstraint(condition=models.Q(quantity__gt=0), name='sale_line_quantity_positive'), models.CheckConstraint(condition=models.Q(price__gte=0), name='sale_line_price_nonnegative'), models.CheckConstraint(condition=models.Q(amount__gte=0), name='sale_line_amount_nonnegative')]


class DocumentEvent(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    document = models.ForeignKey(Document, on_delete=models.PROTECT)
    action = models.CharField(max_length=20)
    version = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
