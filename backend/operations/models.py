from uuid import uuid4
from django.conf import settings
from django.db import models
from django.utils import timezone


class Operation(models.Model):
    class Kind(models.TextChoices):
        RETURN='return','Повернення покупця'
        TRANSFER='transfer','Переміщення'
        WRITEOFF='writeoff','Списання'
    id=models.UUIDField(primary_key=True,default=uuid4,editable=False)
    organization=models.ForeignKey('accounts.Organization',on_delete=models.PROTECT)
    kind=models.CharField(max_length=12,choices=Kind.choices)
    year=models.PositiveIntegerField()
    number=models.PositiveIntegerField()
    date=models.DateField()
    warehouse=models.ForeignKey('catalog.Warehouse',on_delete=models.PROTECT,related_name='+')
    warehouse_name=models.CharField(max_length=200)
    target_warehouse=models.ForeignKey('catalog.Warehouse',null=True,on_delete=models.PROTECT,related_name='+')
    target_name=models.CharField(max_length=200,blank=True)
    source_sale=models.ForeignKey('documents.Document',null=True,on_delete=models.PROTECT)
    source_number=models.CharField(max_length=40,blank=True)
    note=models.CharField(max_length=2000,blank=True)
    status=models.CharField(max_length=16,default='draft',editable=False)
    version=models.PositiveIntegerField(default=1)
    is_active=models.BooleanField(default=True)
    total=models.DecimalField(max_digits=25,decimal_places=2,null=True)
    created_at=models.DateTimeField(default=timezone.now)
    updated_at=models.DateTimeField(default=timezone.now)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['organization','kind','year','number'],name='operation_number_unique'),models.CheckConstraint(condition=models.Q(kind__in=['return','transfer','writeoff']),name='operation_kind_valid')]

    @property
    def display_number(self):
        prefix={'return':'ПВ','transfer':'ПМ','writeoff':'СП'}[self.kind]
        return f'{prefix}-{self.year}-{self.number:06d}'


class OperationLine(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid4,editable=False)
    document=models.ForeignKey(Operation,on_delete=models.CASCADE,related_name='lines')
    position=models.PositiveSmallIntegerField()
    product=models.ForeignKey('catalog.Product',on_delete=models.PROTECT)
    source_line=models.ForeignKey('documents.DocumentLine',null=True,on_delete=models.PROTECT)
    product_name=models.CharField(max_length=200)
    unit_name=models.CharField(max_length=200)
    unit_symbol=models.CharField(max_length=20)
    quantity=models.DecimalField(max_digits=12,decimal_places=3)
    cost=models.DecimalField(max_digits=25,decimal_places=2,null=True)

    class Meta:
        ordering=['position']
        constraints=[models.UniqueConstraint(fields=['document','position'],name='operation_line_order'),models.CheckConstraint(condition=models.Q(quantity__gt=0),name='operation_quantity_positive'),models.CheckConstraint(condition=models.Q(cost__gte=0)|models.Q(cost__isnull=True),name='operation_cost_nonnegative')]


class OperationSequence(models.Model):
    organization=models.ForeignKey('accounts.Organization',on_delete=models.PROTECT)
    kind=models.CharField(max_length=12)
    year=models.PositiveIntegerField()
    last_number=models.PositiveIntegerField(default=0)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['organization','kind','year'],name='operation_sequence_unique')]


class OperationEvent(models.Model):
    organization=models.ForeignKey('accounts.Organization',on_delete=models.PROTECT)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    document=models.ForeignKey(Operation,on_delete=models.PROTECT)
    action=models.CharField(max_length=20)
    version=models.PositiveIntegerField()
    created_at=models.DateTimeField(default=timezone.now)
