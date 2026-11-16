from uuid import uuid4
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Record(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True

    def __str__(self):
        return self.name


class Unit(Record):
    symbol = models.CharField(max_length=20)


class Product(Record):
    class Kind(models.TextChoices):
        GOODS = 'goods', 'Товар'
        SERVICE = 'service', 'Послуга'
    kind = models.CharField(max_length=10, choices=Kind.choices)
    sku = models.CharField(max_length=60, blank=True)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(price__gte=0), name='product_nonnegative_price'), models.CheckConstraint(condition=models.Q(kind__in=['goods', 'service']), name='product_valid_kind')]


class Counterparty(Record):
    class Kind(models.TextChoices):
        COMPANY = 'company', 'Юридична особа'
        SOLE_TRADER = 'sole_trader', 'ФОП'
        PERSON = 'person', 'Фізична особа'
    kind = models.CharField(max_length=16, choices=Kind.choices)
    code = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.CharField(max_length=500, blank=True)
    bank_details = models.CharField(max_length=1000, blank=True)
    note = models.CharField(max_length=2000, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(kind__in=['company', 'sole_trader', 'person']), name='counterparty_valid_kind')]


class Warehouse(Record):
    pass


class CatalogEvent(models.Model):
    organization = models.ForeignKey('accounts.Organization', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    object_id = models.UUIDField()
    action = models.CharField(max_length=20)
    version = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
