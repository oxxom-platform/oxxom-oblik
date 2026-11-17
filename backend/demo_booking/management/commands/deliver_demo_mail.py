from collections import Counter
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from demo_booking.mail import deliver_one
from demo_booking.models import DemoMail


class Command(BaseCommand):
    help = 'Manually deliver synthetic test requests to info@oxxom.com.ua; no automatic timer'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=1)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 10:
            raise CommandError('Limit must be 1–10')
        if options['dry_run']:
            self.stdout.write(json.dumps({'mode': 'dry-run', 'smtp_configured': settings.OXXOM_DEMO_SMTP is not None,
                                          'states': dict(Counter(DemoMail.objects.values_list('state', flat=True)))}))
            return
        if settings.OXXOM_DEMO_SMTP is None:
            raise CommandError('Test email delivery is disabled; requests remain queued. No message was sent.')
        counts = Counter()
        for _ in range(options['limit']):
            state = deliver_one()
            counts[state] += 1
            if state in ('empty', 'disabled', 'uncertain', 'failed'):
                break
        self.stdout.write(json.dumps({'mode': 'synthetic-test-only', 'delivery': dict(counts)}))
