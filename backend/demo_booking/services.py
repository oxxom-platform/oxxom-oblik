from datetime import timedelta
from uuid import UUID

from django.core import signing
from django.db import transaction
from django.utils import timezone

from .models import DemoMail, DemoRequest, DemoThrottle

SALT = 'oxxom.demo-booking.test.v1'
FIXTURE = {
    'name': 'Тестовий користувач', 'company': 'Тестова компанія',
    'phone': '+380000000000', 'email': 'demo@example.test',
    'preferred_date': '', 'preferred_time': '',
    'comment': 'Тестова заявка OXXOM OBLIK. Перевірка форми без контактів відвідувача.',
}


class TooManyRequests(Exception):
    pass


class TestCapacityReached(Exception):
    pass


def new_ticket():
    from uuid import uuid4
    return signing.dumps({'id': str(uuid4()), 'mode': 'test'}, salt=SALT)


def ticket_id(ticket):
    if not isinstance(ticket, str) or len(ticket) > 512:
        raise signing.BadSignature('Invalid ticket')
    value = signing.loads(ticket, salt=SALT, max_age=3600)
    if not isinstance(value, dict) or value.get('mode') != 'test':
        raise signing.BadSignature('Invalid ticket')
    try:
        return UUID(value.get('id', ''))
    except (ValueError, TypeError, AttributeError):
        raise signing.BadSignature('Invalid ticket') from None


@transaction.atomic
def accept_test(request_id, language):
    if language not in ('uk', 'en', 'ru', 'cs'):
        raise ValueError('Unsupported language')
    bucket, _ = DemoThrottle.objects.get_or_create(key='public-tests')
    bucket = DemoThrottle.objects.select_for_update().get(pk=bucket.pk)
    existing = DemoRequest.objects.filter(pk=request_id).first()
    if existing:
        return existing
    now = timezone.now()
    if now - bucket.started >= timedelta(minutes=1):
        bucket.started, bucket.attempts = now, 0
    if bucket.attempts >= 20:
        raise TooManyRequests
    bucket.attempts += 1
    bucket.save(update_fields=['started', 'attempts'])
    # A bounded public prototype. Keep receipts and delivery evidence intact.
    if DemoRequest.objects.count() >= 500:
        raise TestCapacityReached
    lead = DemoRequest.objects.create(id=request_id, language=language, payload=dict(FIXTURE))
    DemoMail.objects.create(request=lead)
    return lead
