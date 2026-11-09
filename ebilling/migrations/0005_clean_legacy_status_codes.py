from django.db import migrations


def clean(apps, schema_editor):
    EBill = apps.get_model('ebilling', 'EBill')
    # Legacy boolean placeholders ('0'/'False'/'True') are not HTTP status codes.
    EBill.objects.filter(
        momo_api_initial_status_code__in=['0', 'False', 'True', '1']
    ).update(momo_api_initial_status_code='')


class Migration(migrations.Migration):

    dependencies = [
        ('ebilling', '0004_ebill_payer_number_and_name'),
    ]

    operations = [
        migrations.RunPython(clean, migrations.RunPython.noop),
    ]
