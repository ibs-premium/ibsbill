"""Pieces shared by the dashboard views: the staff guard, transaction filters and report maths."""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from decouple import config
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, Q, Sum
from django.utils import timezone

from ..models import CallBacks, EBill

STATUSES = ['SUCCESSFUL', 'PENDING', 'FAILED']

# Deduction taken from collected (successful) payments, shown in the reports.
FEE_PERCENT = Decimal(config('COLLECTION_FEE_PERCENT', default='1'))

staff_required = user_passes_test(
    lambda u: u.is_active and u.is_staff, login_url='dashboard:login'
)


def _int(value, default, lo, hi):
    try:
        return min(max(int(value), lo), hi)
    except (TypeError, ValueError):
        return default


def _parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _filtered_bills(params):
    """Transactions matching the Transactions page filters in `params` (GET or POST data)."""
    bills = EBill.objects.all().order_by('-request_datetime')
    q = params.get('q', '').strip()
    status = params.get('status', '')
    app = params.get('app', '')
    date_from = params.get('from', '')
    date_to = params.get('to', '')

    if q:
        bills = bills.filter(
            Q(uuid__icontains=q) | Q(username__icontains=q) | Q(device_id__icontains=q)
            | Q(financial_transaction_id__icontains=q) | Q(momo_number__icontains=q) | Q(momo_name__icontains=q)
        )
    if status:
        bills = bills.filter(status=status)
    if app:
        bills = bills.filter(app=app)
    if date_from:
        bills = bills.filter(request_datetime__date__gte=date_from)
    if date_to:
        bills = bills.filter(request_datetime__date__lte=date_to)
    return bills


TOTALS = dict(
    count=Count('id'),
    successful=Count('id', filter=Q(status='SUCCESSFUL')),
    failed=Count('id', filter=Q(status='FAILED')),
    pending=Count('id', filter=Q(status='PENDING')),
    collected=Sum('amount', filter=Q(status='SUCCESSFUL')),
)


def _with_rate(row):
    """Fill in collected/success_rate on an aggregate row of TOTALS."""
    row['collected'] = row.get('collected') or 0
    finished = row['successful'] + row['failed']
    row['success_rate'] = round(100 * row['successful'] / finished, 1) if finished else None
    return row


def _fee(collected):
    """The deduction on an amount, rounded to 2 decimal places (half up)."""
    amount = Decimal(str(collected or 0))
    return (amount * FEE_PERCENT / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _with_fee(row):
    """Add fee (the deduction) and net (collected minus fee) to a row that has `collected`."""
    # SQLite sums decimals in floating point, so tidy the result back to cents
    row['collected'] = Decimal(str(row['collected'] or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    row['fee'] = _fee(row['collected'])
    row['net'] = row['collected'] - row['fee']
    return row


STALE_AFTER = timedelta(minutes=30)
STALE_WINDOW = timedelta(days=3)


def callback_health():
    """How well MTN's callbacks are arriving, for the dashboard and the Callback Logs page.

    stale_pending counts recent payments that have been waiting over 30 minutes with no callback
    at all. MTN calls back for every request it accepts (approved, declined or expired), so a
    growing number here points at the callback URL not being reachable.
    """
    now = timezone.now()
    last = CallBacks.objects.order_by('-date').values_list('date', flat=True).first()
    return {
        'last': last,
        'last_day': CallBacks.objects.filter(date__gte=now - timedelta(days=1)).count(),
        'attention': CallBacks.objects.filter(date__gte=now - timedelta(days=7),
                                              outcome__in=CallBacks.ATTENTION_OUTCOMES).count(),
        'stale_pending': EBill.objects.filter(
            transaction_complete=False, request_sent=True, callbacks__isnull=True,
            request_datetime__lt=now - STALE_AFTER, request_datetime__gte=now - STALE_WINDOW).count(),
    }
