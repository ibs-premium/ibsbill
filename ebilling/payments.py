"""Recording the final outcome of MTN MoMo payment requests.

Every place that learns a payment's result (the MTN callback, eBilling's status
polling, the dashboard's "Check with MTN" button and the recheck_pending
command) goes through apply_final_status so they all record it the same way.
"""
import json
import logging
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.cache import cache
from django.utils import timezone

from . import mtn
from .models import EBill

logger = logging.getLogger(__name__)

FINAL_STATUSES = ('SUCCESSFUL', 'FAILED')

# MTN's failure reason codes, in plain words for staff.
FAILURE_REASONS = {
    'APPROVAL_REJECTED': 'The customer declined the prompt, or entered the wrong PIN.',
    'EXPIRED': 'The customer did not approve in time (PIN not entered before the prompt expired).',
    'TRANSACTION_CANCELED': 'The payment was cancelled.',
    'NOT_ENOUGH_FUNDS': "Not enough money in the customer's MoMo wallet.",
    'PAYER_NOT_FOUND': 'The number is not a registered MTN MoMo account.',
    'PAYER_LIMIT_REACHED': "The customer has reached their MoMo transaction limit.",
    'PAYER_NOT_ALLOWED': 'The customer is not allowed to make this payment.',
    'NOT_ALLOWED': 'MTN does not allow this payment.',
    'INTERNAL_PROCESSING_ERROR': 'MTN had a problem processing the payment. Try again.',
    'SERVICE_UNAVAILABLE': 'MTN MoMo was unavailable. Try again.',
    'COULD_NOT_PERFORM_TRANSACTION': 'MTN could not complete the transaction. Try again.',
    'RESOURCE_NOT_FOUND': 'MTN has no record of this payment.',
    'PAYEE_NOT_FOUND': 'Our MoMo collection account was not found by MTN (configuration problem).',
    'NOT_ALLOWED_TARGET_ENVIRONMENT': 'MTN environment mismatch (configuration problem).',
    'INVALID_CURRENCY': 'Currency not accepted by MTN (configuration problem).',
    'INVALID_CALLBACK_URL_HOST': 'The callback URL was rejected by MTN (configuration problem).',
    'REQUEST_REFUSED': 'MTN refused the payment request.',
}


def failure_text(code):
    """Plain-words explanation for a stored MTN failure reason ('' if there is none)."""
    if not code:
        return ''
    return FAILURE_REASONS.get(code, str(code).replace('_', ' ').capitalize())


def extract_reason(data):
    """The failure reason code from an MTN status payload. MTN sends it either as a
    string or as {"code": ..., "message": ...}."""
    reason = data.get('reason') if isinstance(data, dict) else None
    if isinstance(reason, dict):
        reason = reason.get('code') or reason.get('message')
    return str(reason)[:100] if reason else ''
TOKEN_CACHE_KEY = 'mtn_collection_token'
TOKEN_CACHE_SECONDS = 50 * 60  # MTN tokens last an hour; renew before that
DUPLICATE_WINDOW_SECONDS = 60


def get_mtn_token():
    """An MTN access token, reused across requests until shortly before it expires."""
    token = cache.get(TOKEN_CACHE_KEY)
    if token is None:
        token = mtn.get_token()
        if token is not None:
            cache.set(TOKEN_CACHE_KEY, token, TOKEN_CACHE_SECONDS)
    return token


def apply_final_status(ebill, status, financial_transaction_id='', reason=''):
    """Record a SUCCESSFUL/FAILED result on an EBill once.

    Returns True if the record changed. Non-final statuses (e.g. PENDING) and
    already-completed records are left alone, so repeated callbacks are harmless.
    """
    if status not in FINAL_STATUSES or ebill.transaction_complete:
        return False
    ebill.status = status
    ebill.transaction_complete = True
    ebill.request_completed_datetime = timezone.now()
    if financial_transaction_id:
        ebill.financial_transaction_id = financial_transaction_id
    if status == 'FAILED' and reason:
        ebill.failure_reason = reason
    ebill.save(update_fields=['status', 'transaction_complete', 'request_completed_datetime',
                              'financial_transaction_id', 'failure_reason'])
    return True


def record_payer(ebill, number='', token=None):
    """Fill in the payer's MoMo number and name on an EBill if they're missing.

    The number comes from MTN's payloads (payer.partyId); with a token the name is
    looked up from MTN. Existing values are never overwritten.
    """
    fields = []
    if number and not ebill.momo_number:
        ebill.momo_number = number
        fields.append('momo_number')
    if token and ebill.momo_number and not ebill.momo_name:
        name = mtn.get_account_holder_name(token, ebill.momo_number)
        if name:
            ebill.momo_name = name
            fields.append('momo_name')
    if fields:
        ebill.save(update_fields=fields)


def payer_number(mtn_data):
    """The payer's MSISDN from an MTN callback/status payload, or ''."""
    payer = mtn_data.get('payer') if isinstance(mtn_data, dict) else None
    return payer.get('partyId', '') if isinstance(payer, dict) else ''


def check_with_mtn(ebill, token):
    """Ask MTN for the current status of an EBill and record it if final.

    Returns (outcome, mtn_status) where outcome is one of
    'updated', 'unchanged', 'not_found' or 'error'.
    """
    result = mtn.check_status(ebill.uuid, token)
    if result.get('status_code') == 404:
        return 'not_found', None
    if result.get('status_code') == 401:
        cache.delete(TOKEN_CACHE_KEY)  # token rejected; fetch a fresh one next time
        return 'error', 'MTN rejected the access token; try again'
    if not result.get('success'):
        return 'error', result.get('message')
    try:
        data = json.loads(result.get('raw_response_text') or '{}')
    except json.JSONDecodeError:
        return 'error', 'MTN returned an unreadable response'
    status = data.get('status')
    record_payer(ebill, payer_number(data), token)
    changed = apply_final_status(ebill, status, data.get('financialTransactionId', ''), extract_reason(data))
    return ('updated' if changed else 'unchanged'), status


def parse_amount(value):
    """A positive amount rounded to 2 decimal places, or None if it isn't usable."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount >= Decimal('10000000'):
            return None
        amount = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount > 0 else None


def start_payment(app_name, amount, momo_number, device_id, username, caller_ip, caller_url):
    """Send an MTN request-to-pay and record it. Returns (http_status, response_body).

    The caller must already have authenticated whoever is asking. The payment is
    saved *before* MTN is contacted, so a request that reaches MTN always has a
    record, even if something fails straight afterwards.
    """
    amount = parse_amount(amount)
    if amount is None:
        return 400, {'status': 'error', 'message': 'Invalid amount!'}
    validated = mtn.validate_momo_number(str(momo_number or ''))
    if validated is None:
        return 400, {'status': 'error', 'message': 'Invalid MoMo number!'}
    momo_number = validated

    token = get_mtn_token()
    if token is None:
        return 503, {'status': 'error', 'message': 'Could not obtain access token. Please try again.'}

    # Registered MoMo name of the payer, for records and receipts ('' if MTN won't say)
    momo_name = mtn.get_account_holder_name(token, momo_number)

    # The same request repeated within a minute (a double click, a client retry) must not send the
    # customer a second prompt: answer with the one already sent.
    recent = EBill.objects.filter(
        app=app_name, device_id=device_id or '', momo_number=momo_number, amount=amount, status='PENDING',
        request_sent=True, request_datetime__gte=timezone.now() - timedelta(seconds=DUPLICATE_WINDOW_SECONDS),
    ).order_by('-request_datetime').first()
    if recent is not None:
        return 200, {
            'ok': True,
            'momo_request': {'success': True, 'request_reference_id': recent.uuid},
            'payer': {'momo_number': momo_number, 'momo_name': recent.momo_name},
            'duplicate': True,
        }

    ref_id = str(uuid.uuid4())
    ebill = EBill.objects.create(
        app=app_name, app_ip=caller_ip or '', app_url=caller_url, amount=amount, username=username or '',
        uuid=ref_id, device_id=device_id or '', momo_number=momo_number, momo_name=momo_name,
        request_sent=False, status='PENDING',
    )

    # externalId is echoed back in MTN's callback body, so it must be our reference
    # (the X-Reference-Id header we send is not included in the callback).
    result = mtn.request_to_pay(token, amount, momo_number, ref_id, external_id=ref_id)
    if result.get('status_code') == 401:  # cached token was rejected; retry once with a fresh one
        cache.delete(TOKEN_CACHE_KEY)
        token = get_mtn_token()
        if token:
            result = mtn.request_to_pay(token, amount, momo_number, ref_id, external_id=ref_id)

    status_code = result.get('status_code')
    message = result.get('message')
    success = result.get('success', False)
    ebill.momo_api_initial_status_code = str(status_code)
    ebill.momo_api_initial_message = message
    ebill.request_sent = bool(success)
    ebill.save(update_fields=['momo_api_initial_status_code', 'momo_api_initial_message', 'request_sent'])

    if not success:
        # A 4xx means MTN refused the request, so no prompt went out: close it as FAILED.
        # Anything else (timeout, network, 5xx) is uncertain, so it stays PENDING for the
        # recheck to settle against MTN.
        if status_code is not None and 400 <= status_code < 500:
            details = result.get('error_details')
            code = details.get('code') if isinstance(details, dict) else ''
            apply_final_status(ebill, 'FAILED', reason=str(code or 'REQUEST_REFUSED')[:100])
        logger.warning('MTN request-to-pay not accepted for %s: %s %s', ref_id, status_code, message)
        return 200, {'ok': False, 'momo_request': {'success': False, 'message': message}}

    return 200, {
        'ok': True,
        'momo_request': {'success': True, 'request_reference_id': ref_id},
        'payer': {'momo_number': momo_number, 'momo_name': momo_name},
    }


def payment_status(ebill):
    """Current status of a payment as (http_status, body), asking MTN if it isn't final yet."""
    if ebill.status in FINAL_STATUSES:
        return 200, {
            'raw_response_text': json.dumps({'status': ebill.status, 'reference_id': ebill.uuid}),
            'status': ebill.status,
            'reason_code': ebill.failure_reason,
            'reason': failure_text(ebill.failure_reason),
            'source': 'cache',
        }
    token = get_mtn_token()
    if token is None:
        return 503, {'error': 'Could not obtain access token. Please try again.'}
    result = mtn.check_status(ebill.uuid, token)
    if result.get('status_code') == 401:
        cache.delete(TOKEN_CACHE_KEY)
    raw_text = result.get('raw_response_text') or '{}'
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        data = {}
    if isinstance(data, dict) and data.get('status') in FINAL_STATUSES:
        record_payer(ebill, payer_number(data))
        apply_final_status(ebill, data['status'], data.get('financialTransactionId', ''), extract_reason(data))
    return 200, {
        'success': result.get('success', False),
        'status_code': result.get('status_code'),
        'reference_id': ebill.uuid,
        'message': result.get('message'),
        'raw_response_text': raw_text,
        'status': data.get('status') if isinstance(data, dict) else None,
        'reason_code': ebill.failure_reason,
        'reason': failure_text(ebill.failure_reason),
        'source': 'mtn',
    }
