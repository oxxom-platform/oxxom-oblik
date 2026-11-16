import importlib
from types import SimpleNamespace
from django.apps import apps
from django.db import connection
from django.test import TestCase
from accounts.bootstrap import create_owner
from accounts.models import Organization
from .defaults import ensure_initial_units
from .models import Unit


class InitialUnitsTests(TestCase):
    def test_new_owner_has_units_for_goods_and_services(self):
        _, organization = create_owner(email='units@example.com', password='Only-For-Tests-Password-782!', organization_name='Units')
        self.assertEqual(set(Unit.objects.filter(organization=organization).values_list('symbol', flat=True)), {'шт', 'год', 'посл'})

    def test_initialization_is_idempotent_and_organization_scoped(self):
        organization = Organization.objects.create(name='A')
        other = Organization.objects.create(name='B')
        ensure_initial_units(organization)
        ensure_initial_units(organization)
        self.assertEqual(Unit.objects.filter(organization=organization).count(), 3)
        self.assertFalse(Unit.objects.filter(organization=other).exists())

    def test_existing_or_archived_units_are_not_replaced(self):
        organization = Organization.objects.create(name='A')
        unit = Unit.objects.create(organization=organization, name='Custom', symbol='c', is_active=False)
        ensure_initial_units(organization)
        unit.refresh_from_db()
        self.assertFalse(unit.is_active)
        self.assertEqual(Unit.objects.filter(organization=organization).count(), 1)

    def test_migration_backfills_only_empty_organizations_and_can_repeat(self):
        empty = Organization.objects.create(name='Empty')
        configured = Organization.objects.create(name='Configured')
        Unit.objects.create(organization=configured, name='Custom', symbol='custom')
        archived = Organization.objects.create(name='Archived')
        Unit.objects.create(organization=archived, name='Archived', symbol='a', is_active=False)
        seed = importlib.import_module('catalog.migrations.0002_initial_units').seed_empty_organizations
        seed(apps, SimpleNamespace(connection=connection))
        seed(apps, SimpleNamespace(connection=connection))
        self.assertEqual(Unit.objects.filter(organization=empty).count(), 3)
        self.assertEqual(Unit.objects.filter(organization=configured).count(), 1)
        self.assertFalse(Unit.objects.filter(organization=archived, is_active=True).exists())
