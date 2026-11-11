import ipaddress

from django.core.exceptions import ValidationError
from django.db import models


def split_networks(value):
    """The addresses and ranges in a comma- or space-separated list, as ip_network objects."""
    items = [item for item in str(value or '').replace(',', ' ').split() if item]
    return [ipaddress.ip_network(item, strict=False) for item in items]


def validate_ip_list(value):
    """One or more IP addresses or ranges (e.g. 10.0.4.170, 10.0.5.0/24), separated by commas."""
    try:
        networks = split_networks(value)
    except ValueError as exc:
        raise ValidationError(f'Enter IP addresses or ranges separated by commas ({exc}).')
    if not networks:
        raise ValidationError('Enter at least one IP address.')


class Application(models.Model):
    name = models.CharField(max_length=30, verbose_name='Name of Application')
    url = models.CharField(max_length=255, verbose_name='url of app')
    key = models.CharField(max_length=64)
    ip_address = models.CharField(
        max_length=255, validators=[validate_ip_list], verbose_name='IP addresses allowed',
        help_text='Addresses the application calls from: one or more IP addresses or ranges, separated by '
                  'commas, e.g. 10.0.4.170, 10.0.5.0/24')

    def allows_ip(self, ip):
        """True if `ip` is one of this application's registered addresses or inside one of its ranges."""
        try:
            address = ipaddress.ip_address(str(ip).strip())
            return any(address in network for network in split_networks(self.ip_address))
        except ValueError:
            return False

    def __str__(self):
        return self.name


class EBill(models.Model):
    app = models.CharField(max_length=30)
    app_ip = models.CharField(max_length=50)
    app_url = models.CharField(max_length=500)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    username = models.CharField(max_length=50)
    uuid = models.CharField(max_length=255, unique=True)
    device_id = models.CharField(max_length=255, verbose_name='Device Id, e.g. Meter serial number')
    momo_number = models.CharField(max_length=15, blank=True, default='', verbose_name='Payer MoMo number')
    momo_name = models.CharField(max_length=255, blank=True, default='', verbose_name='Payer MoMo name')

    # MTN MoMo request state
    request_sent = models.BooleanField(default=False)
    momo_api_initial_status_code = models.CharField(max_length=50, null=True, blank=True, default='')
    momo_api_initial_message = models.TextField(null=True, blank=True)
    financial_transaction_id = models.CharField(max_length=255, null=True, blank=True, default='')
    status = models.CharField(max_length=50, null=True, blank=True, default='PENDING')
    failure_reason = models.CharField(max_length=100, blank=True, default='',
                                      help_text='Why the payment failed, as reported by MTN (e.g. EXPIRED)')

    # Completion tracking
    transaction_complete = models.BooleanField(default=False)
    request_datetime = models.DateTimeField(auto_now_add=True)
    request_completed_datetime = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Request from {self.app} | {self.uuid} | {self.request_datetime}"


class CallBacks(models.Model):
    # What the gateway did with a callback (set once it has been looked at)
    OUTCOME_CHOICES = [
        ('processed', 'Recorded'),
        ('already_processed', 'Already recorded'),
        ('pending', 'Still pending'),
        ('unconfirmed', 'Not confirmed by MTN'),
        ('mismatch', 'Disagrees with MTN'),
        ('unknown_reference', 'Unknown reference'),
        ('unverified', 'Could not check with MTN'),
        ('invalid', 'Not valid JSON'),
    ]
    OUTCOME_HELP = {
        'processed': 'MTN confirmed the result and it was recorded on the payment.',
        'already_processed': 'The payment already had its final result, so nothing changed.',
        'pending': 'MTN says the payment is still waiting for the customer.',
        'unconfirmed': 'The callback claimed a final result but MTN did not confirm it. Usually MTN\'s status '
                       'had not caught up yet and the next check settles it; it could also be a forged callback.',
        'mismatch': 'The callback claimed one final result and MTN reports a different one. The result from '
                    'MTN was recorded. Look into this one.',
        'unknown_reference': 'The callback names a payment we have no record of. MTN only calls back about '
                             'payments we sent, so this is unexpected.',
        'unverified': 'The gateway could not reach MTN to confirm the result, so nothing was recorded. '
                      'Check the payment with MTN.',
        'invalid': 'The body was not valid JSON, so it could not be matched to a payment.',
    }
    # Outcomes worth a person looking at
    ATTENTION_OUTCOMES = ('mismatch', 'unconfirmed', 'unknown_reference', 'unverified', 'invalid')

    ebill = models.ForeignKey('EBill', null=True, blank=True, on_delete=models.SET_NULL, related_name='callbacks',
                              verbose_name='Payment')
    reference = models.CharField(max_length=255, blank=True, default='', db_index=True,
                                 verbose_name='Reference named in the callback')
    claimed_status = models.CharField(max_length=50, blank=True, default='',
                                      help_text='The status the callback said, which is not trusted')
    verified_status = models.CharField(max_length=50, blank=True, default='',
                                       help_text='The status MTN reported when the gateway checked')
    outcome = models.CharField(max_length=30, blank=True, default='', choices=OUTCOME_CHOICES, db_index=True)
    url = models.CharField(max_length=255, verbose_name='URL of app')
    method = models.CharField(max_length=10, verbose_name='HTTP Method')
    headers = models.TextField(blank=True, null=True, verbose_name='Request Headers')
    contents = models.TextField(verbose_name='Raw Request Body')
    query_params = models.TextField(blank=True, null=True, verbose_name='Query Parameters')
    ip_address = models.GenericIPAddressField(verbose_name='Client IP Address')
    user_agent = models.CharField(max_length=512, blank=True, null=True, verbose_name='User-Agent')
    referrer = models.CharField(max_length=512, blank=True, null=True, verbose_name='HTTP Referrer')
    date = models.DateTimeField(auto_now_add=True, verbose_name='Received At')

    def __str__(self):
        return f"Callback from {self.ip_address} at {self.date}"