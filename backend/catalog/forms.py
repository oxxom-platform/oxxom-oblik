from django import forms
from django.db.models import Q
from .models import Unit, Product, Counterparty, Warehouse


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'kind', 'sku', 'unit', 'price']

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        available = Q(is_active=True)
        if self.instance.pk and self.instance.unit_id:
            available |= Q(pk=self.instance.unit_id)
        self.fields['unit'].queryset = Unit.objects.filter(available, organization=organization)


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ['name', 'symbol']


class CounterpartyForm(forms.ModelForm):
    class Meta:
        model = Counterparty
        fields = ['name', 'kind', 'code', 'email', 'phone', 'address', 'bank_details', 'note']


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ['name']


FORMS = {'products': ProductForm, 'units': UnitForm, 'counterparties': CounterpartyForm, 'warehouses': WarehouseForm}
MODELS = {key: form._meta.model for key, form in FORMS.items()}
