"""Raw MTN callback logs."""
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from ..models import CallBacks
from .common import callback_health, staff_required


@staff_required
def callbacks(request):
    logs = CallBacks.objects.select_related('ebill').order_by('-date')
    q = request.GET.get('q', '').strip()
    outcome = request.GET.get('outcome', '')
    if q:
        logs = logs.filter(Q(contents__icontains=q) | Q(ip_address__icontains=q) | Q(reference__icontains=q))
    if outcome == 'attention':
        logs = logs.filter(outcome__in=CallBacks.ATTENTION_OUTCOMES)
    elif outcome in dict(CallBacks.OUTCOME_CHOICES):
        logs = logs.filter(outcome=outcome)
    else:
        outcome = ''
    page = Paginator(logs, 50).get_page(request.GET.get('page'))
    params = {k: v for k, v in (('q', q), ('outcome', outcome)) if v}
    return render(request, 'dashboard/callbacks.html', {
        'nav': 'callbacks',
        'page': page,
        'q': q,
        'outcome': outcome,
        'outcomes': CallBacks.OUTCOME_CHOICES,
        'health': callback_health(),
        'querystring': urlencode(params),
    })


@staff_required
def callback_detail(request, pk):
    log = get_object_or_404(CallBacks.objects.select_related('ebill'), pk=pk)
    return render(request, 'dashboard/callback_detail.html', {
        'nav': 'callbacks',
        'log': log,
    })
