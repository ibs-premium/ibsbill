"""Transaction list, detail and the checks against MTN."""
import csv

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import EBill
from ..payments import apply_final_status, check_with_mtn, failure_text, get_mtn_token
from .common import STATUSES, _filtered_bills, _int, staff_required


@staff_required
def transactions(request):
    bills = _filtered_bills(request.GET)

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="transactions.csv"'
        writer = csv.writer(response)
        writer.writerow(['Date', 'Reference', 'App', 'Username', 'Payer name', 'Payer number', 'Device ID',
                         'Amount', 'Status', 'Reason for failure', 'Financial Transaction ID', 'Completed'])
        for b in bills:
            writer.writerow([b.request_datetime.isoformat(), b.uuid, b.app, b.username, b.momo_name,
                             b.momo_number, b.device_id,
                             b.amount, b.status, failure_text(b.failure_reason), b.financial_transaction_id,
                             b.request_completed_datetime.isoformat() if b.request_completed_datetime else ''])
        return response

    page = Paginator(bills, 50).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)

    return render(request, 'dashboard/transactions.html', {
        'nav': 'transactions',
        'page': page,
        'statuses': STATUSES,
        'apps': EBill.objects.values_list('app', flat=True).distinct().order_by('app'),
        'filters': request.GET,
        'querystring': params.urlencode(),
        'total_amount': bills.aggregate(s=Sum('amount'))['s'] or 0,
        'unfinished_count': bills.filter(transaction_complete=False).count(),
    })


@staff_required
def transaction_detail(request, pk):
    bill = get_object_or_404(EBill, pk=pk)
    return render(request, 'dashboard/transaction_detail.html', {
        'nav': 'transactions',
        'bill': bill,
        'callbacks': bill.callbacks.order_by('date'),
    })


@staff_required
@require_POST
def transaction_check(request, pk):
    """Ask MTN for this transaction's current status and record it if final."""
    bill = get_object_or_404(EBill, pk=pk)
    token = get_mtn_token()
    if token is None:
        messages.error(request, 'Could not get an access token from MTN. Check the MTN credentials and try again.')
        return redirect('dashboard:transaction_detail', pk=pk)

    outcome, status = check_with_mtn(bill, token)
    if outcome == 'updated':
        messages.success(request, f'MTN reports {status}. The transaction has been updated.')
    elif outcome == 'unchanged':
        messages.info(request, f'MTN reports {status or "no status"}. Nothing changed.')
    elif outcome == 'not_found':
        messages.error(request, 'MTN has no record of this reference. The request probably never reached MTN.')
    else:
        messages.error(request, f'Could not check with MTN: {status}')
    return redirect('dashboard:transaction_detail', pk=pk)


CHECK_BATCH_SIZE = 15


@staff_required
@require_POST
def transactions_check(request):
    """Check one batch of the filtered, unfinished transactions with MTN.

    The page calls this repeatedly, passing the last id it saw as `after`, so a
    large backlog is worked through in short requests instead of one that times out.
    """
    unfinished = _filtered_bills(request.POST).filter(transaction_complete=False).order_by('pk')
    after = _int(request.POST.get('after'), 0, 0, 2 ** 62)
    fail_not_found = request.POST.get('fail_not_found') == '1'
    batch = list(unfinished.filter(pk__gt=after)[:CHECK_BATCH_SIZE])
    if not batch:
        return JsonResponse({'done': True, 'checked': 0, 'after': after, 'remaining': 0, 'counts': {}})

    token = get_mtn_token()
    if token is None:
        return JsonResponse({'error': 'Could not get an access token from MTN. Check the MTN credentials.'},
                            status=503)

    counts = {'successful': 0, 'failed': 0, 'pending': 0, 'not_found': 0, 'error': 0}
    for bill in batch:
        outcome, status = check_with_mtn(bill, token)
        if outcome == 'updated':
            counts['successful' if status == 'SUCCESSFUL' else 'failed'] += 1
        elif outcome == 'unchanged':
            counts['pending'] += 1
        elif outcome == 'not_found':
            if fail_not_found and apply_final_status(bill, 'FAILED'):
                counts['failed'] += 1
            else:
                counts['not_found'] += 1
        else:
            counts['error'] += 1

    last = batch[-1].pk
    remaining = unfinished.filter(pk__gt=last).count()
    return JsonResponse({'done': remaining == 0, 'checked': len(batch), 'after': last,
                         'remaining': remaining, 'counts': counts})
