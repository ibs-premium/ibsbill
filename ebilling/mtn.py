"""Client for MTN's MoMo Collection API.

Every call has a timeout so a slow MTN cannot tie up a worker, and none of them
log tokens or credentials.
"""
import base64
import logging

import requests
from decouple import config

logger = logging.getLogger(__name__)

SUBSCRIPTION_KEY = config('MTN_SUBSCRIPTION_KEY')
API_USER = config('MTN_API_USER')
API_KEY = config('MTN_API_KEY')
CALLBACK_URL = config('MTN_CALLBACK_URL')
CURRENCY = config('MTN_CURRENCY')
ENVIRONMENT = config('MTN_ENVIRONMENT')

BASE_URL = 'https://proxy.momoapi.mtn.com/collection'
TIMEOUT = 15  # seconds, for every request to MTN


def _headers(token, **extra):
    return {
        'Authorization': f'Bearer {token}',
        'X-Target-Environment': ENVIRONMENT,
        'Ocp-Apim-Subscription-Key': SUBSCRIPTION_KEY,
        **extra,
    }


def get_token():
    """A fresh access token from MTN, or None if it could not be obtained."""
    credentials = base64.b64encode(f'{API_USER}:{API_KEY}'.encode()).decode()
    try:
        response = requests.post(
            f'{BASE_URL}/token/',
            headers={'Authorization': f'Basic {credentials}', 'Ocp-Apim-Subscription-Key': SUBSCRIPTION_KEY},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        logger.error('MTN token request failed: %s', exc)
        return None
    if response.status_code != 200:
        logger.error('MTN token request returned HTTP %s', response.status_code)
        return None
    return response.json().get('access_token')


def _error_details(response):
    try:
        return response.json()
    except ValueError:
        return {'raw_response_text': response.text}


def check_status(ref_id, token):
    """Ask MTN for the status of a payment request.

    Returns a dict with success, status_code, reference_id, message and
    raw_response_text (status_code is None if MTN could not be reached).
    """
    try:
        response = requests.get(f'{BASE_URL}/v1_0/requesttopay/{ref_id}', headers=_headers(token),
                                timeout=TIMEOUT)
    except requests.exceptions.RequestException as exc:
        logger.warning('MTN status check for %s failed: %s', ref_id, exc)
        return {'success': False, 'status_code': None, 'reference_id': ref_id,
                'message': f'Could not reach MTN: {exc}', 'error_details': {'exception': str(exc)}}

    result = {'success': response.status_code == 200, 'status_code': response.status_code,
              'reference_id': ref_id, 'raw_response_text': response.text}
    if response.status_code == 200:
        try:
            result['payment_status'] = response.json().get('status')
            result['message'] = f"Payment status retrieved: {result['payment_status']}"
        except ValueError:
            result['message'] = 'Status check succeeded, but the response was not valid JSON.'
    else:
        details = _error_details(response)
        result['error_details'] = details
        detail_message = details.get('message') if isinstance(details, dict) else None
        result['message'] = f'Status check failed with HTTP {response.status_code}: {detail_message or response.text}'
    return result


def request_to_pay(token, amount, payer_number, ref_id, external_id):
    """Ask MTN to prompt a customer for payment.

    Returns a dict with success (True only for 202 Accepted), status_code (None if MTN
    could not be reached), message and, when refused, error_details.
    """
    body = {
        'amount': str(amount),
        'currency': CURRENCY,
        'externalId': external_id,
        'payer': {'partyIdType': 'MSISDN', 'partyId': payer_number},
        'payerMessage': 'Please approve payment for purchase of credit to your meter.',
        'payeeNote': 'Thank you for using IBS eBilling. Your meter will be credited immediately.',
    }
    base = {'reference_id': ref_id, 'external_id': external_id}
    try:
        response = requests.post(
            f'{BASE_URL}/v1_0/requesttopay',
            headers=_headers(token, **{'X-Reference-Id': ref_id, 'X-Callback-Url': CALLBACK_URL,
                                       'Content-Type': 'application/json'}),
            json=body, timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        # Uncertain: MTN may or may not have received it
        logger.warning('MTN request-to-pay %s failed: %s', ref_id, exc)
        return {**base, 'success': False, 'status_code': None,
                'message': f'Network or request error: {exc}', 'error_details': {'exception': str(exc)}}

    if response.status_code == 202:
        logger.info('MTN accepted request-to-pay %s', ref_id)
        return {**base, 'success': True, 'status_code': 202, 'message': 'Payment request accepted for processing.'}
    logger.warning('MTN refused request-to-pay %s: HTTP %s', ref_id, response.status_code)
    return {**base, 'success': False, 'status_code': response.status_code,
            'message': 'Payment request failed with API error.', 'error_details': _error_details(response)}


def get_account_holder_name(token, msisdn):
    """Registered MoMo name for a number (e.g. 233241234567), or '' if MTN won't say.

    Failures return '' so a missing name never blocks a payment.
    """
    try:
        response = requests.get(f'{BASE_URL}/v1_0/accountholder/msisdn/{msisdn}/basicuserinfo',
                                headers=_headers(token), timeout=10)
        if response.status_code != 200:
            return ''
        data = response.json()
    except (requests.exceptions.RequestException, ValueError):
        return ''
    name = data.get('name') or ' '.join(filter(None, [data.get('given_name'), data.get('family_name')]))
    return name.strip()


def validate_momo_number(momo_number):
    """The number as 233XXXXXXXXX if it is a valid MTN Ghana number, else None."""
    momo_number = (momo_number or '').strip()
    if not momo_number.isdigit() or len(momo_number) not in (10, 12):
        return None
    if len(momo_number) == 10:
        if not momo_number.startswith('0') or momo_number.startswith(('057', '027', '020', '050')):
            return None
        return '233' + momo_number[1:]
    if not momo_number.startswith('233') or momo_number[3] == '0':
        return None
    if momo_number.startswith(('23357', '23327', '23320', '23350')):
        return None
    return momo_number
