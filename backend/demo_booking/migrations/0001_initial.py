import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='DemoRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('product', models.CharField(default='oxxom-oblik', editable=False, max_length=20)),
                ('is_test', models.BooleanField(default=True, editable=False)),
                ('language', models.CharField(max_length=2)),
                ('source', models.CharField(default='/demo/', editable=False, max_length=20)),
                ('status', models.CharField(default='new', editable=False, max_length=12)),
                ('payload', models.JSONField()),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
        ),
        migrations.CreateModel(
            name='DemoThrottle',
            fields=[
                ('key', models.CharField(max_length=40, primary_key=True, serialize=False)),
                ('started', models.DateTimeField(default=django.utils.timezone.now)),
                ('attempts', models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name='DemoMail',
            fields=[
                ('request', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='demo_booking.demorequest')),
                ('state', models.CharField(db_index=True, default='queued', max_length=16)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('next_attempt', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('lease_until', models.DateTimeField(null=True)),
                ('sent_at', models.DateTimeField(null=True)),
                ('error_code', models.CharField(blank=True, max_length=60)),
            ],
        ),
        migrations.AddConstraint(
            model_name='demorequest',
            constraint=models.CheckConstraint(condition=models.Q(('is_test', True)), name='demo_fixture_only'),
        ),
        migrations.AddConstraint(
            model_name='demorequest',
            constraint=models.CheckConstraint(condition=models.Q(('product', 'oxxom-oblik')), name='demo_product_oblik'),
        ),
        migrations.AddConstraint(
            model_name='demorequest',
            constraint=models.CheckConstraint(condition=models.Q(('language__in', ['uk', 'en', 'ru', 'cs'])), name='demo_supported_language'),
        ),
    ]
