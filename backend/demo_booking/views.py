from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST, require_safe

from core.i18n import COOKIE, language as safe_language
from core.seo import ORIGIN, LANGUAGES, asset_url, context, render_public
from .copy import strings
from .services import FIXTURE, TooManyRequests, TestCapacityReached, accept_test, new_ticket, ticket_id


def path_for(language):
    return ('/' if language == 'uk' else '/' + language + '/') + 'demo/'


@never_cache
@require_safe
@ensure_csrf_cookie
def page(request, language='uk'):
    values = context(language)
    copy = strings(language)
    values.update(copy=copy, fixture=FIXTURE, ticket=new_ticket(), home_path=LANGUAGES[language]['path'])
    values['seo'].update(title=copy['title'] + ' — OXXOM OBLIK', description=copy['description'],
                         path=path_for(language), url=ORIGIN + path_for(language))
    values['seo_alternates'] = [dict(item, language=code, path=path_for(code), url=ORIGIN + path_for(code)) for code, item in LANGUAGES.items()]
    values['landing_data']['urls'] = {code: path_for(code) for code in LANGUAGES}
    values['booking_data'] = {'language': language, 'messages': {key: copy[key] for key in ('success', 'failure', 'limited', 'expired', 'sending', 'submit')}}
    values['booking_css'] = asset_url('demo-booking.css', '/assets/landing/demo-booking.css')
    values['booking_js'] = asset_url('demo-booking.js', '/assets/landing/demo-booking.js')
    markup = render_public(render_to_string('demo_booking/page.html', values, request=request), language)
    response = HttpResponse(markup)
    # The prototype is deliberately not an indexable live booking service.
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Content-Language'] = language
    response['Referrer-Policy'] = 'no-referrer'
    response['Content-Security-Policy'] = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                          "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
                                          "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'")
    return response


@never_cache
@require_POST
@sensitive_post_parameters()
@sensitive_variables()
def submit(request):
    language = safe_language(request.COOKIES.get(COOKIE))
    copy = strings(language)
    # Fields supplied by the browser are never used as contacts, comments, UTM,
    # or mail headers. Only a signed test-ticket and honeypot are read.
    if request.POST.get('website', ''):
        return JsonResponse({'error': 'invalid_request', 'message': copy['failure']}, status=400)
    try:
        identifier = ticket_id(request.POST.get('ticket', ''))
    except signing.BadSignature:
        return JsonResponse({'error': 'expired_ticket', 'message': copy['expired']}, status=400)
    try:
        lead = accept_test(identifier, language)
    except TooManyRequests:
        response = JsonResponse({'error': 'rate_limited', 'message': copy['limited']}, status=429)
        response['Retry-After'] = '60'
        return response
    except (DatabaseError, TestCapacityReached):
        return JsonResponse({'error': 'temporarily_unavailable', 'message': copy['failure']}, status=503)
    response = JsonResponse({'status': 'test_received', 'id': str(lead.pk), 'message': copy['success']})
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response
