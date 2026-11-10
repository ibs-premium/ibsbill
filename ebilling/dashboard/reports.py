"""Period reports with CSV and PDF export."""
import calendar
import csv
from datetime import date, timedelta
from decimal import Decimal

from django.db.models.functions import TruncDate, TruncMonth
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from ..models import EBill
from ..report_pdf import build_report_pdf
from .common import FEE_PERCENT, TOTALS, _int, _parse_date, _with_fee, _with_rate, staff_required


PERIODS = [
    ('monthly', 'Monthly'),
    ('quarterly', 'Quarterly'),
    ('half', 'Half-yearly'),
    ('yearly', 'Yearly'),
    ('custom', 'Selected period'),
]


def _month_span(year, first_month, months):
    """First and last day of a run of whole months."""
    last_month = first_month + months - 1
    return date(year, first_month, 1), date(year, last_month, calendar.monthrange(year, last_month)[1])


def _report_range(params, today):
    """Resolve the report period from the query string.

    Returns (period, start, end, label, prev_start, prev_end, selection) with
    inclusive dates; prev_* is the equivalent period immediately before.
    """
    period = params.get('period') if params.get('period') in dict(PERIODS) else 'monthly'
    year = _int(params.get('year'), today.year, 2000, 2100)
    sel = {'year': year}

    if period == 'monthly':
        month = _int(params.get('month'), today.month, 1, 12)
        sel['month'] = month
        start, end = _month_span(year, month, 1)
        label = start.strftime('%B %Y')
        prev_end = start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
    elif period == 'quarterly':
        quarter = _int(params.get('quarter'), (today.month - 1) // 3 + 1, 1, 4)
        sel['quarter'] = quarter
        start, end = _month_span(year, 3 * quarter - 2, 3)
        label = f'Q{quarter} {year} ({start:%b} – {end:%b})'
        prev_start, prev_end = (_month_span(year, 3 * quarter - 5, 3) if quarter > 1
                                else _month_span(year - 1, 10, 3))
    elif period == 'half':
        half = _int(params.get('half'), 1 if today.month <= 6 else 2, 1, 2)
        sel['half'] = half
        start, end = _month_span(year, 1 if half == 1 else 7, 6)
        label = f'H{half} {year} ({start:%b} – {end:%b})'
        prev_start, prev_end = _month_span(year, 1, 6) if half == 2 else _month_span(year - 1, 7, 6)
    elif period == 'yearly':
        start, end = date(year, 1, 1), date(year, 12, 31)
        label = str(year)
        prev_start, prev_end = date(year - 1, 1, 1), date(year - 1, 12, 31)
    else:
        start = _parse_date(params.get('from')) or today.replace(day=1)
        end = _parse_date(params.get('to')) or today
        if end < start:
            start, end = end, start
        sel.update({'from': start.isoformat(), 'to': end.isoformat()})
        label = f'{start:%d %b %Y} – {end:%d %b %Y}'
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - (end - start)

    return period, start, end, label, prev_start, prev_end, sel


def _buckets(start, end):
    """Breakdown rows for the range: days for up to ~2 months, months beyond that."""
    if (end - start).days <= 62:
        return 'day', [start + timedelta(days=i) for i in range((end - start).days + 1)]
    months, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return 'month', months


def _delta(now, before):
    if not before:
        return None
    return round(100 * (now - before) / before, 1)


@staff_required
def reports(request):
    today = timezone.localdate()
    period, start, end, label, prev_start, prev_end, sel = _report_range(request.GET, today)
    app = request.GET.get('app', '')

    bills = EBill.objects.filter(request_datetime__date__gte=start, request_datetime__date__lte=end)
    prev = EBill.objects.filter(request_datetime__date__gte=prev_start, request_datetime__date__lte=prev_end)
    if app:
        bills, prev = bills.filter(app=app), prev.filter(app=app)

    totals = _with_fee(_with_rate(bills.aggregate(**TOTALS)))
    prev_totals = _with_fee(_with_rate(prev.aggregate(**TOTALS)))
    totals['average'] = totals['collected'] / totals['successful'] if totals['successful'] else 0
    deltas = {
        'collected': _delta(totals['collected'], prev_totals['collected']),
        'count': _delta(totals['count'], prev_totals['count']),
    }

    # Breakdown by day or month, with empty buckets filled in
    unit, buckets = _buckets(start, end)
    if unit == 'day':
        grouped = bills.annotate(bucket=TruncDate('request_datetime'))
        key = lambda d: d
    else:
        grouped = bills.annotate(bucket=TruncMonth('request_datetime'))
        key = lambda d: (d.year, d.month)
    found = {key(row['bucket']): row for row in grouped.values('bucket').annotate(**TOTALS)}
    rows = []
    for bucket in buckets:
        row = found.get(key(bucket))
        row = _with_rate(dict(row) if row else {'count': 0, 'successful': 0, 'failed': 0, 'pending': 0})
        row['date'] = bucket
        row['label'] = bucket.strftime('%a %d %b' if unit == 'day' else '%B %Y')
        rows.append(_with_fee(row))
    # The total is the sum of the rows so the columns add up exactly
    totals['fee'] = sum((r['fee'] for r in rows), Decimal('0'))
    totals['net'] = totals['collected'] - totals['fee']

    by_app = [_with_fee(_with_rate(r)) for r in bills.values('app').annotate(**TOTALS).order_by('-collected', '-count')]

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="momo_report_{start}_{end}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Report', label])
        writer.writerow(['From', start.isoformat(), 'To', end.isoformat()])
        writer.writerow(['Application', app or 'All'])
        writer.writerow([])
        writer.writerow(['Day' if unit == 'day' else 'Month', 'Requests', 'Successful', 'Failed', 'Pending',
                         'Success rate %', 'Collected', f'Deduction ({FEE_PERCENT.normalize():f}%)', 'Net'])
        for r in rows + [dict(totals, date=None)]:
            when = 'Total' if r['date'] is None else (
                r['date'].isoformat() if unit == 'day' else r['date'].strftime('%Y-%m'))
            rate = r['success_rate'] if r['success_rate'] is not None else ''
            writer.writerow([when, r['count'], r['successful'], r['failed'], r['pending'],
                             rate, f"{r['collected']:.2f}", f"{r['fee']:.2f}", f"{r['net']:.2f}"])
        return response

    first = EBill.objects.order_by('request_datetime').values_list('request_datetime', flat=True).first()
    first_year = first.year if first else today.year
    params = request.GET.copy()
    params.pop('export', None)

    context = {
        'nav': 'reports',
        'periods': PERIODS,
        'period': period,
        'sel': sel,
        'app': app,
        'apps': EBill.objects.values_list('app', flat=True).distinct().order_by('app'),
        'years': range(max(today.year, sel['year']), min(first_year, sel['year']) - 1, -1),
        'months': [(i, calendar.month_name[i]) for i in range(1, 13)],
        'label': label,
        'start': start,
        'end': end,
        'prev_start': prev_start,
        'prev_end': prev_end,
        'totals': totals,
        'prev_totals': prev_totals,
        'deltas': deltas,
        'unit': unit,
        'rows': rows,
        'chart': [{'label': r['label'], 'count': r['count'], 'amount': float(r['collected'])} for r in rows],
        'by_app': by_app,
        'fee_percent': f'{FEE_PERCENT.normalize():f}',
        'querystring': params.urlencode(),
        'generated': timezone.localtime(),
    }

    if request.GET.get('export') == 'pdf':
        response = HttpResponse(build_report_pdf(dict(context, username=request.user.get_username())),
                                content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="momo_report_{start}_{end}.pdf"'
        return response

    return render(request, 'dashboard/reports.html', context)
