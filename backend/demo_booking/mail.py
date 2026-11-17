"""Manual test-mail delivery. No public request can choose recipient or headers."""
from datetime import timedelta
import errno
import smtplib

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.db import transaction
from django.utils import timezone

from .models import DemoMail
from .services import FIXTURE

RECIPIENT = 'info@oxxom.com.ua'


def claim():
    now = timezone.now()
    with transaction.atomic():
        # An expired send might already have reached SMTP. Never auto-resend it.
        DemoMail.objects.filter(state='sending', lease_until__lte=now).update(
            state='uncertain', lease_until=None, error_code='expired_delivery_lease')
        job = (DemoMail.objects.select_for_update(skip_locked=True)
               .filter(state='queued', next_attempt__lte=now, attempts__lt=5)
               .order_by('next_attempt', 'request_id').first())
        if job is None:
            return None
        job.state, job.lease_until = 'sending', now + timedelta(seconds=120)
        job.attempts += 1
        job.save(update_fields=['state', 'lease_until', 'attempts'])
        return job


def outcome(error):
    """Retry explicit transient rejection only; unknown delivery is manual review."""
    if isinstance(error, (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused)):
        return 'failed'
    if isinstance(error, smtplib.SMTPResponseException):
        return 'queued' if 400 <= error.smtp_code < 500 else 'failed'
    if isinstance(error, ConnectionRefusedError) or isinstance(error, OSError) and error.errno == errno.ECONNREFUSED:
        return 'queued'
    return 'uncertain'


def deliver_one():
    config = settings.OXXOM_DEMO_SMTP
    if config is None:
        return 'disabled'
    job = claim()
    if job is None:
        return 'empty'
    body = ('ТЕСТОВА ЗАЯВКА OXXOM OBLIK — не є реальним зверненням.\n\n'
            + '\n'.join(f'{key}: {value}' for key, value in FIXTURE.items())
            + '\n\nTest request ID: ' + str(job.request_id))
    try:
        with get_connection(backend='django.core.mail.backends.smtp.EmailBackend', **config) as connection:
            email = EmailMessage(subject='[OXXOM ОБЛІК][TEST] Тестова заявка — Тестова компанія',
                                 body=body, from_email=RECIPIENT, to=[RECIPIENT],
                                 connection=connection,
                                 headers={'Message-ID': '<demo-test-' + str(job.request_id) + '@oblik.oxxom.com.ua>'})
            if email.send(fail_silently=False) != 1:
                raise RuntimeError('Delivery not confirmed')
        state, error_code = 'sent', ''
    except Exception as error:
        state, error_code = outcome(error), type(error).__name__[:60]
        if state == 'queued' and job.attempts >= 5:
            state = 'failed'
    with transaction.atomic():
        current = DemoMail.objects.select_for_update().get(pk=job.pk)
        if current.state != 'sending' or current.attempts != job.attempts:
            # A stale worker never overwrites a review decision.
            return 'uncertain'
        current.state, current.error_code, current.lease_until = state, error_code, None
        if state == 'sent':
            current.sent_at = timezone.now()
        elif state == 'queued':
            current.next_attempt = timezone.now() + timedelta(seconds=min(3600, 60 * 2 ** (job.attempts - 1)))
        current.save(update_fields=['state', 'error_code', 'lease_until', 'sent_at', 'next_attempt'])
    return state
