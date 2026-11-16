from django.db import transaction
from accounts.models import Organization
from .models import Unit

DEFAULT_UNITS = [('Штука', 'шт'), ('Година', 'год'), ('Послуга', 'посл')]


@transaction.atomic
def ensure_initial_units(organization):
    """Initialize an empty catalog only. Never reactivate or replace user units."""
    Organization.objects.select_for_update().get(pk=organization.pk)
    if not Unit.objects.filter(organization=organization).exists():
        Unit.objects.bulk_create([Unit(organization=organization, name=name, symbol=symbol) for name, symbol in DEFAULT_UNITS])
