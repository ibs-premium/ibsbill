import json

from django import template
from decouple import config

register = template.Library()

CURRENCY = config('MTN_CURRENCY', default='')


@register.filter
def money(value):
    try:
        amount = f"{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return value
    return f"{CURRENCY} {amount}".strip()


@register.filter
def status_class(status):
    return {
        'SUCCESSFUL': 'success',
        'FAILED': 'failed',
        'PENDING': 'pending',
    }.get(status, 'unknown')


@register.filter
def page_window(page, size=3):
    """Page numbers to show in the pager: `size` consecutive pages around the current one.

    Normally the previous, current and next page; at either end the window slides
    so it still shows `size` numbers (e.g. 1 2 3 on the first page).
    """
    total = page.paginator.num_pages
    size = min(int(size), total)
    start = max(1, min(page.number - size // 2, total - size + 1))
    return range(start, start + size)


@register.filter
def pretty_json(value):
    """Indent a JSON string for display; return it unchanged if it isn't JSON."""
    try:
        return json.dumps(json.loads(value), indent=2)
    except (TypeError, ValueError):
        return value
