from uuid import uuid4
from django.conf import settings
from django.db import models
from django.utils import timezone


class StockDraft(models.Model):
    class Kind(models.TextChoices):
        OPENING = 'opening', 'Початкові залишки'
        RECEIPT = 'receipt', 'Надходження'
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    year = models.PositiveIntegerField()
    number = models.PositiveIntegerField()
    date = models.DateField()
    warehouse = models.ForeignKey('catalog.Warehouse', on_delete=models.PROTECT)
    warehouse_name = models.CharField(max_length=200)
    counterparty = models.ForeignKey('catalog.Counterparty', null=True, blank=True, on_delete=models.PROTECT)
    counterparty_snapshot = models.JSONField(default=dict)
    note = models.CharField(max_length=2000, blank=True)
    status = models.CharField(max_length=16, default='draft', editable=False)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    total = models.DecimalField(max_digits=25, decimal_places=2, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'kind', 'year', 'number'], name='stock_draft_number_unique'), models.CheckConstraint(condition=models.Q(kind__in=['opening', 'receipt']), name='stock_draft_valid_kind'), models.CheckConstraint(condition=models.Q(total__gte=0) | models.Q(total__isnull=True), name='stock_draft_nonnegative_total')]

    @property
    def display_number(self):
        prefix = 'ПЗ' if self.kind == 'opening' else 'НД'
        return f'{prefix}-{self.year}-{self.number:06d}'


class StockSequence(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    kind = models.CharField(max_length=10)
    year = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'kind', 'year'], name='stock_sequence_unique')]


class StockLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    document = models.ForeignKey(StockDraft, on_delete=models.CASCADE, related_name='lines')
    position = models.PositiveSmallIntegerField()
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    product_name = models.CharField(max_length=200)
    unit_name = models.CharField(max_length=200)
    unit_symbol = models.CharField(max_length=20)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    price = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    amount = models.DecimalField(max_digits=23, decimal_places=2, null=True)

    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['document', 'position'], name='stock_line_position_unique'), models.CheckConstraint(condition=models.Q(quantity__gt=0), name='stock_line_positive_quantity'), models.CheckConstraint(condition=models.Q(price__gte=0) | models.Q(price__isnull=True), name='stock_line_nonnegative_price')]


class StockEvent(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    document = models.ForeignKey(StockDraft, on_delete=models.PROTECT)
    action = models.CharField(max_length=20)
    version = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
