import hmac
import ipaddress
import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Application, CallBacks, EBill
from .payments import FINAL_STATUSES, check_with_mtn, get_mtn_token, payment_status, start_payment
from .ratelimit import rate_limit

logger = logging.getLogger(__name__)


def client_ip(request):
    """The caller's IP address.

    Behind nginx the real address is in X-Forwarded-For. Only the entry added by
    our own proxy is trusted (counting from the right), because anything further
    left can be written by the caller. TRUSTED_PROXY_COUNT is 0 with no proxy.
    """
    remote = request.META.get('REMOTE_ADDR', '')
    count = settings.TRUSTED_PROXY_COUNT
    forwarded = [p.strip() for p in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if p.strip()]
    if count and len(forwarded) >= count:
        return forwarded[-count]
    return remote


def _same_ip(a, b):
    try:
        return ipaddress.ip_address(str(a).strip()) == ipaddress.ip_address(str(b).strip())
    except ValueError:
        return False


def authenticate_app(request, name, key):
    """The registered Application if `key` (and, unless disabled, the caller's IP) match, else None."""
    app = Application.objects.filter(name=name).first() if name else None
    # Always compare something so a wrong name and a wrong key take the same time
    expected = app.key if app else ''
    key_ok = hmac.compare_digest(str(key or '').encode(), expected.encode()) and app is not None
    if not key_ok:
        logger.warning('Rejected /bill/ call for app %r: bad name or key (ip %s)', name, client_ip(request))
        return None
    if settings.ENFORCE_APP_IP and not _same_ip(client_ip(request), app.ip_address):
        logger.warning('Rejected /bill/ call for app %r: ip %s is not the registered %s',
                       name, client_ip(request), app.ip_address)
        return None
    return app


@csrf_exempt
@rate_limit('bill', limit=30)
def bill(request):
    """eBilling app calls this to initiate an MTN MoMo payment request."""
    if request.method != 'POST':
        return HttpResponse('Invalid Request', status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    app = authenticate_app(request, data.get('app'), data.get('app_key'))
    if app is None:
        return JsonResponse({"status": "Failed", "message": "Invalid application credentials"}, status=403)

    status, body = start_payment(
        app_name=app.name,
        amount=data.get('amount'),
        momo_number=data.get('momo_number'),
        device_id=data.get('device_id'),
        username=data.get('username'),
        caller_ip=client_ip(request),
        caller_url=request.build_absolute_uri(),
    )
    return JsonResponse(body, status=status)


@csrf_exempt
@rate_limit('callback', limit=240)
def mtn_callback(request):
    """
    MTN MoMo calls this URL when a payment is finalised.

    The body is not trusted: anyone can POST to this URL. It is only used to find
    the payment; the result is then confirmed by asking MTN directly, so a forged
    callback cannot mark a payment paid. eBilling then picks the result up by
    polling /bill/callback/. MTN delivers request-to-pay callbacks as PUT; POST is
    accepted too.
    """
    if request.method not in ('PUT', 'POST'):
        return JsonResponse({"error": "Method not allowed"}, status=405)

    # Log the raw callback for audit purposes
    log = None
    try:
        log = CallBacks.objects.create(
            url=request.build_absolute_uri(),
            method=request.method,
            headers=str({k: v for k, v in request.headers.items() if k.lower() != 'authorization'}),
            contents=request.body.decode('utf-8', errors='replace'),
            query_params=str(dict(request.GET)),
            ip_address=client_ip(request) or '0.0.0.0',
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            referrer=request.META.get('HTTP_REFERER', ''),
        )
    except Exception:
        logger.exception('Could not store the callback log')  # never block the callback response

    def record(outcome, ebill=None, reference='', claimed='', verified=''):
        """Note on the log what the gateway made of this callback."""
        if log is None:
            return
        try:
            log.outcome, log.ebill = outcome, ebill
            log.reference, log.claimed_status, log.verified_status = reference[:255], claimed[:50], verified[:50]
            log.save(update_fields=['outcome', 'ebill', 'reference', 'claimed_status', 'verified_status'])
        except Exception:
            logger.exception('Could not update the callback log')

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        record('invalid')
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # Our reference arrives as externalId (see start_payment); the other fields and the
    # header are checked too in case MTN's payload format differs.
    candidates = [ref for ref in (data.get('externalId'),
                                  data.get('referenceId'),
                                  data.get('reference_id'),
                                  data.get('X-Reference-Id'),
                                  request.headers.get('X-Reference-Id')) if ref and isinstance(ref, str)]
    claimed = str(data.get('status') or '').upper()

    ebill = EBill.objects.filter(uuid__in=candidates).first() if candidates else None
    if ebill is None:
        # Unknown reference — respond 200 so MTN doesn't retry
        record('unknown_reference', reference=candidates[0] if candidates else '', claimed=claimed)
        return JsonResponse({"ok": True, "message": "Callback acknowledged"})

    # Idempotency: completed records are left unchanged
    if ebill.transaction_complete:
        record('already_processed', ebill, ebill.uuid, claimed, ebill.status or '')
        return JsonResponse({"ok": True, "message": "Already processed"})

    token = get_mtn_token()
    if token is None:
        # Stays PENDING; eBilling's polling or recheck_pending will settle it
        record('unverified', ebill, ebill.uuid, claimed)
        return JsonResponse({"ok": True, "message": "Callback received; result will be confirmed with MTN"})

    result, mtn_status = check_with_mtn(ebill, token)
    if result in ('error', 'not_found'):
        record('unverified', ebill, ebill.uuid, claimed)
    elif claimed in FINAL_STATUSES and mtn_status in FINAL_STATUSES and claimed != mtn_status:
        record('mismatch', ebill, ebill.uuid, claimed, mtn_status)
        logger.warning('Callback for %s said %s but MTN says %s', ebill.uuid, claimed, mtn_status)
    elif claimed in FINAL_STATUSES and mtn_status not in FINAL_STATUSES:
        record('unconfirmed', ebill, ebill.uuid, claimed, mtn_status or '')
    elif result == 'updated':
        record('processed', ebill, ebill.uuid, claimed, mtn_status or '')
    else:
        record('pending', ebill, ebill.uuid, claimed, mtn_status or '')

    if result == 'updated':
        return JsonResponse({"ok": True, "message": "Callback received and processed"})
    return JsonResponse({"ok": True, "message": f"MTN reports {mtn_status or 'no status'}; nothing recorded"})


@csrf_exempt
@rate_limit('status', limit=240)
def bill_status(request):
    """
    eBilling polls this endpoint to check if a MoMo payment has been finalised.
    Returns our recorded status if it is final, otherwise asks MTN directly and
    records the result. Only references we issued are answered, and our own MTN
    credentials are used, so callers never need (or receive) an MTN token.
    """
    if request.method != 'POST':
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    reference_id = data.get('reference_id')
    if not reference_id or not isinstance(reference_id, str):
        return JsonResponse({"error": "reference_id is required"}, status=400)

    ebill = EBill.objects.filter(uuid=reference_id).first()
    if ebill is None:
        return JsonResponse({"error": "Unknown reference"}, status=404)

    status, body = payment_status(ebill)
    return JsonResponse(body, status=status)
