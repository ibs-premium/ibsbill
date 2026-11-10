import json

from django.db import migrations


def link_existing_callbacks(apps, schema_editor):
    """Match callbacks received before outcomes were tracked to their payments.

    Only the reference, payment and claimed status are filled in. The outcome stays blank, because
    what the gateway made of those callbacks was never recorded.
    """
    CallBacks = apps.get_model('ebilling', 'CallBacks')
    EBill = apps.get_model('ebilling', 'EBill')
    for log in CallBacks.objects.filter(reference='').iterator():
        try:
            data = json.loads(log.contents)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        refs = [r for r in (data.get('externalId'), data.get('referenceId'), data.get('reference_id')) if
                r and isinstance(r, str)]
        if not refs:
            continue
        log.reference = refs[0][:255]
        log.claimed_status = str(data.get('status') or '').upper()[:50]
        log.ebill = EBill.objects.filter(uuid__in=refs).first()
        log.save(update_fields=['reference', 'claimed_status', 'ebill'])


class Migration(migrations.Migration):
    dependencies = [('ebilling', '0008_callback_outcome')]

    operations = [migrations.RunPython(link_existing_callbacks, migrations.RunPython.noop)]
