"""Dashboard home and the MoMo payment page."""
from datetime import timedelta
import json

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import Application, EBill
from ..payments import payment_status, start_payment
from ..views import client_ip
from .common import callback_health, FEE_PERCENT, TOTALS, _with_fee, _with_rate, staff_required


@staff_required
def overview(request):
    bills = EBill.objects.all()
    totals = _with_fee(_with_rate(bills.aggregate(**TOTALS)))

    # Daily volume for the last 14 days, with empty days filled in
    today = timezone.localdate()
    start = today - timedelta(days=13)
    daily = {
        row['day']: row
        for row in bills.filter(request_datetime__date__gte=start)
        .annotate(day=TruncDate('request_datetime'))
        .values('day')
        .annotate(
            count=Count('id'),
            amount=Sum('amount', filter=Q(status='SUCCESSFUL')),
        )
    }
    chart = []
    for i in range(14):
        day = start + timedelta(days=i)
        row = daily.get(day, {})
        chart.append({
            'label': day.strftime('%d %b'),
            'count': row.get('count', 0),
            'amount': float(row.get('amount') or 0),
        })

    by_app = (
        bills.values('app')
        .annotate(
            count=Count('id'),
            successful=Count('id', filter=Q(status='SUCCESSFUL')),
            collected=Sum('amount', filter=Q(status='SUCCESSFUL')),
        )
        .order_by('-count')
    )

    return render(request, 'dashboard/overview.html', {
        'nav': 'overview',
        'totals': totals,
        'fee_percent': f'{FEE_PERCENT.normalize():f}',
        'chart': chart,
        'by_app': by_app,
        'recent': bills.order_by('-request_datetime')[:8],
        'app_count': Application.objects.count(),
        'health': callback_health(),
    })


@staff_required
def pay(request):
    """Send a MoMo payment request through /bill/ and follow it to completion."""
    return render(request, 'dashboard/pay.html', {
        'nav': 'pay',
        'apps': Application.objects.order_by('name'),
    })


@staff_required
@require_POST
def pay_start(request):
    """Start a payment from the dashboard form. Staff are already signed in, so the
    application's API key and IP address (for programs calling /bill/) don't apply."""
    try:
        data = json.loads(request.body)
        app = Application.objects.filter(name=data.get('app')).first()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    if app is None:
        return JsonResponse({'status': 'error', 'message': 'Choose a registered application.'}, status=400)
    status, body = start_payment(
        app_name=app.name,
        amount=data.get('amount'),
        momo_number=data.get('momo_number'),
        device_id=data.get('device_id'),
        username=data.get('username') or request.user.get_username(),
        caller_ip=client_ip(request),
        caller_url=request.build_absolute_uri(),
    )
    return JsonResponse(body, status=status)


@staff_required
@require_POST
def pay_status(request):
    """Status of a payment started from the dashboard (asks MTN until it is final)."""
    try:
        reference = json.loads(request.body).get('reference_id')
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    bill = EBill.objects.filter(uuid=reference).first() if isinstance(reference, str) else None
    if bill is None:
        return JsonResponse({'error': 'Unknown reference'}, status=404)
    status, body = payment_status(bill)
    return JsonResponse(body, status=status)
