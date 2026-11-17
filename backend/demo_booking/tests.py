from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
import json
import re
import smtplib
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.core.management import call_command
from django.db import close_old_connections
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from . import mail
from .copy import catalog
from .models import DemoMail, DemoRequest, DemoThrottle
from .services import FIXTURE, SALT, TooManyRequests, TestCapacityReached, accept_test, new_ticket


class BookingPageTests(SimpleTestCase):
    def test_four_languages_and_conflicting_cookie_with_no_db_or_auth(self):
        for code, prefix in [('uk',''), ('en','en/'), ('ru','ru/'), ('cs','cs/')]:
            self.client.cookies['oxxom_language'] = 'ru' if code != 'ru' else 'en'
            response = self.client.get('/' + prefix + 'demo/?email=PRIVATE_VISITOR')
            with self.subTest(code=code):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Language'], code)
                self.assertContains(response, 'lang="' + code + '"')
                self.assertContains(response, catalog()[code]['test_notice'])
                self.assertContains(response, 'mailto:info@oxxom.com.ua')
                self.assertContains(response, 'demo@example.test')
                self.assertNotContains(response, 'PRIVATE_VISITOR')
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(response.cookies['oxxom_language'].value, code)
                self.assertNotContains(response, 'name="email"')
                self.assertNotContains(response, 'name="phone"')
                self.assertNotContains(response, 'privacy_consent')
                self.assertNotContains(response, 'landing.js?')
                self.assertIn("script-src 'self'", response['Content-Security-Policy'])
                expected = '/' + prefix + 'demo/'
                self.assertContains(response, 'rel="canonical" href="https://oblik.oxxom.com.ua' + expected + '"')
                for language, other_prefix in [('uk',''), ('en','en/'), ('ru','ru/'), ('cs','cs/')]:
                    self.assertContains(response, 'href="/' + other_prefix + 'demo/" lang="' + language + '"')

    def test_landing_ctas_follow_language_and_footer_email_is_public(self):
        for prefix in ('', 'en/', 'ru/', 'cs/'):
            response = self.client.get('/' + prefix)
            self.assertContains(response, 'href="/' + prefix + 'demo/"')
            self.assertContains(response, 'mailto:info@oxxom.com.ua')
            self.assertNotContains(response, 'Публічний канал для комерційних звернень ще готується')

    def test_copy_keys_are_complete_and_no_fake_calendar_booking_or_company_details(self):
        source = set(catalog()['uk'])
        for code, copy in catalog().items():
            self.assertEqual(set(copy), source, code)
            self.assertTrue(all(isinstance(value, str) and value for value in copy.values()))
        response = self.client.get('/demo/')
        self.assertNotContains(response, 'ЄДРПОУ')
        self.assertNotContains(response, 'ІПН')
        self.assertNotContains(response, 'name="privacy_consent"')


class BookingSubmissionTests(TestCase):
    url = '/api/public/demo-requests'

    def post(self, payload, client=None, **extra):
        return (client or self.client).post(
            self.url, urlencode(payload),
            content_type='application/x-www-form-urlencoded', **extra)

    def test_submission_ignores_all_visitor_data_and_is_idempotent(self):
        ticket = new_ticket()
        payload = {'ticket':ticket, 'email':'PRIVATE_CONTACT@example.com', 'company':'PRIVATE_COMPANY',
                   'comment':'PRIVATE_COMMENT<script>alert(1)</script>', 'phone':'+12345678900',
                   'product':'oxxom-hub', 'privacy_consent':'yes'}
        first = self.post(payload)
        second = self.post(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        lead = DemoRequest.objects.get()
        self.assertEqual(lead.payload, FIXTURE)
        self.assertEqual(lead.product, 'oxxom-oblik')
        self.assertTrue(lead.is_test)
        self.assertEqual(DemoMail.objects.count(), 1)
        self.assertNotIn('PRIVATE', json.dumps(first.json()))
        self.assertIn('no-store', first['Cache-Control'])
        self.assertEqual(first['X-Robots-Tag'], 'noindex, nofollow')

    def test_csrf_is_required_and_normal_token_succeeds(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(self.post({'ticket':new_ticket()}, client=client).status_code, 403)
        page = client.get('/demo/')
        token = client.cookies['csrftoken'].value
        ticket = re.search(rb'name="ticket" value="([^"]+)"', page.content).group(1).decode()
        response = self.post({'ticket':ticket}, client=client, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post({'ticket':new_ticket()}, client=client, HTTP_X_CSRFTOKEN=token,
                                     HTTP_ORIGIN='https://attacker.invalid').status_code, 403)

    def test_bad_expired_ticket_and_honeypot_do_not_create_mail_or_lead(self):
        for payload in [{'ticket':'bad'}, {'ticket':'a'*513}, {'ticket':new_ticket(),'website':'spam'}]:
            self.assertEqual(self.post(payload).status_code, 400)
        with patch('django.core.signing.time.time', return_value=0):
            expired = new_ticket()
        self.assertEqual(self.post({'ticket':expired}).status_code, 400)
        self.assertFalse(DemoRequest.objects.exists())
        self.assertFalse(DemoMail.objects.exists())

    def test_limit_is_global_without_storing_client_metadata_and_keeps_old_receipts(self):
        first_ticket = new_ticket()
        first = self.post({'ticket':first_ticket})
        for i in range(19):
            self.assertEqual(self.post({'ticket':new_ticket()}, REMOTE_ADDR=f'198.51.100.{i}').status_code, 200)
        limited = self.post({'ticket':new_ticket()})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited['Retry-After'], '60')
        self.assertEqual(self.post({'ticket':first_ticket}).json(), first.json())
        self.assertEqual(list(DemoThrottle.objects.values_list('key', flat=True)), ['public-tests'])
        DemoThrottle.objects.update(started=timezone.now()-timedelta(minutes=2))
        self.assertEqual(self.post({'ticket':new_ticket()}).status_code, 200)

    def test_uploads_and_oversized_bodies_are_rejected_before_csrf_parsing(self):
        client = Client(enforce_csrf_checks=True)
        with patch('django.http.request.HttpRequest._load_post_and_files',
                   side_effect=AssertionError('Request parsed too early')) as parse:
            response = client.post(self.url, {'ticket':new_ticket()})
            self.assertEqual(response.status_code, 415)
            self.assertEqual(response.json()['error'], 'unsupported_media_type')
            response = self.post({'ticket':new_ticket(), 'extra':'x'*5000}, client=client)
            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.json()['error'], 'payload_too_large')
            parse.assert_not_called()
        self.assertFalse(DemoRequest.objects.exists())
        self.assertFalse(DemoMail.objects.exists())

    def test_atomic_queue_failure_rolls_back_lead(self):
        with patch('demo_booking.services.DemoMail.objects.create', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                accept_test(uuid4(), 'uk')
        self.assertEqual(DemoRequest.objects.count(), 0)

    def test_capacity_stops_before_writing_and_does_not_delete_delivery_evidence(self):
        with patch('demo_booking.services.DemoRequest.objects.count', return_value=500):
            response = self.post({'ticket':new_ticket()})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(DemoRequest.objects.count(), 0)


@override_settings(OXXOM_DEMO_SMTP={'host':'smtp.example.test','port':587,'username':'test','password':'synthetic','use_tls':True,'use_ssl':False,'timeout':10})
class BookingMailTests(TestCase):
    def setUp(self):
        self.lead = accept_test(uuid4(), 'uk')

    def test_disabled_and_dry_run_do_not_send_or_change_queue(self):
        with override_settings(OXXOM_DEMO_SMTP=None), patch.object(mail, 'get_connection') as connect:
            self.assertEqual(mail.deliver_one(), 'disabled')
            output = StringIO()
            call_command('deliver_demo_mail', dry_run=True, stdout=output)
            self.assertIn('dry-run', output.getvalue())
            connect.assert_not_called()
        self.assertEqual(DemoMail.objects.get().state, 'queued')

    def test_synthetic_email_has_fixed_recipient_header_and_only_one_send(self):
        from django.core import mail as django_mail
        connection = django_mail.get_connection('django.core.mail.backends.locmem.EmailBackend')
        with patch.object(mail, 'get_connection', return_value=connection):
            self.assertEqual(mail.deliver_one(), 'sent')
            self.assertEqual(mail.deliver_one(), 'empty')
        self.assertEqual(len(django_mail.outbox), 1)
        message = django_mail.outbox[0]
        self.assertEqual(message.to, ['info@oxxom.com.ua'])
        self.assertEqual(message.from_email, 'info@oxxom.com.ua')
        self.assertIn('[TEST]', message.subject)
        self.assertIn('demo@example.test', message.body)
        self.assertEqual(message.extra_headers['Message-ID'], '<demo-test-' + str(self.lead.pk) + '@oblik.oxxom.com.ua>')
        self.assertIsNotNone(DemoMail.objects.get().sent_at)

    def test_explicit_temporary_rejection_retries_with_backoff(self):
        with patch.object(mail, 'get_connection'), patch.object(mail.EmailMessage, 'send', side_effect=smtplib.SMTPDataError(451,b'temporary')):
            self.assertEqual(mail.deliver_one(), 'queued')
        job = DemoMail.objects.get()
        self.assertGreater(job.next_attempt, timezone.now())
        self.assertEqual(job.error_code, 'SMTPDataError')
        with patch.object(mail, 'get_connection') as connect:
            self.assertEqual(mail.deliver_one(), 'empty')
            connect.assert_not_called()

    def test_timeout_or_crash_after_possible_delivery_never_auto_resends(self):
        with patch.object(mail, 'get_connection'), patch.object(mail.EmailMessage, 'send', side_effect=TimeoutError('possible send')):
            self.assertEqual(mail.deliver_one(), 'uncertain')
        self.assertEqual(mail.deliver_one(), 'empty')
        DemoMail.objects.update(state='sending', lease_until=timezone.now()-timedelta(seconds=1))
        self.assertEqual(mail.deliver_one(), 'empty')
        self.assertEqual(DemoMail.objects.get().state, 'uncertain')

    def test_permanent_failure_has_no_secret_values_in_output(self):
        with patch.object(mail, 'get_connection', side_effect=smtplib.SMTPAuthenticationError(535,b'PRIVATE_PASSWORD')):
            self.assertEqual(mail.deliver_one(), 'failed')
        job = DemoMail.objects.get()
        self.assertEqual(job.error_code, 'SMTPAuthenticationError')
        self.assertNotIn('PRIVATE_PASSWORD', job.error_code)


class ConcurrentBookingTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_same_signed_nonce_creates_one_request_and_one_outbox_row(self):
        identifier = uuid4()
        def submit(_):
            close_old_connections()
            try:
                return accept_test(identifier, 'uk').pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(submit, range(4)))
        self.assertEqual(set(ids), {identifier})
        self.assertEqual(DemoRequest.objects.count(), 1)
        self.assertEqual(DemoMail.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_parallel_mail_workers_claim_once(self):
        lead = accept_test(uuid4(), 'uk')
        def claim(_):
            close_old_connections()
            try:
                job = mail.claim()
                return str(job.pk) if job else None
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(claim, range(4)))
        self.assertEqual(ids.count(str(lead.pk)), 1)
