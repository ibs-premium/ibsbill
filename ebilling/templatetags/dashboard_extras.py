import json

from django import template
from decouple import config

from ..payments import failure_text as _failure_text

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
def outcome_class(outcome):
    """Badge colour for a callback outcome."""
    return {
        'processed': 'success', 'already_processed': 'unknown', 'pending': 'unknown',
        'unconfirmed': 'pending', 'unverified': 'pending',
        'mismatch': 'failed', 'unknown_reference': 'failed', 'invalid': 'failed',
    }.get(outcome, 'unknown')


@register.filter
def outcome_help(outcome):
    """One-sentence explanation of a callback outcome."""
    from ..models import CallBacks
    return CallBacks.OUTCOME_HELP.get(outcome, 'Received before outcomes were recorded.')


@register.filter
def failure_text(code):
    """Plain-words explanation of a stored MTN failure reason code."""
    return _failure_text(code)


@register.filter
def pretty_json(value):
    """Indent a JSON string for display; return it unchanged if it isn't JSON."""
    try:
        return json.dumps(json.loads(value), indent=2)
    except (TypeError, ValueError):
        return value
