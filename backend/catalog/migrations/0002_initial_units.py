from django.db import migrations


def seed_empty_organizations(apps, schema_editor):
    Organization = apps.get_model('accounts', 'Organization')
    Unit = apps.get_model('catalog', 'Unit')
    alias = schema_editor.connection.alias
    for organization in Organization.objects.using(alias).all().iterator():
        if not Unit.objects.using(alias).filter(organization_id=organization.pk).exists():
            Unit.objects.using(alias).bulk_create([
                Unit(organization_id=organization.pk, name=name, symbol=symbol)
                for name, symbol in [('Штука', 'шт'), ('Година', 'год'), ('Послуга', 'посл')]
            ])


class Migration(migrations.Migration):
    dependencies = [('catalog', '0001_initial')]
    operations = [migrations.RunPython(seed_empty_organizations, migrations.RunPython.noop)]
