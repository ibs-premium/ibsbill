import json
import uuid
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import Application, CallBacks, EBill


def mtn_status_response(status, code=200, financial_id='FIN-1', payer=None):
    """Shape of this.check_status() output for a given MTN status."""
    if code != 200:
        return {'success': False, 'status_code': code, 'raw_response_text': '{}', 'message': 'not found'}
    body = {'status': status, 'financialTransactionId': financial_id, 'externalId': 'x'}
    if payer:
        body['payer'] = {'partyIdType': 'MSISDN', 'partyId': payer}
    return {'success': True, 'status_code': 200, 'raw_response_text': json.dumps(body)}


# Pin the settings the tests rely on so a developer's .env can't change the outcome
@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   ENFORCE_APP_IP=True, TRUSTED_PROXY_COUNT=1)
class PaymentTestCase(TestCase):
    def setUp(self):
        cache.clear()  # don't let a cached MTN token leak between tests

    def make_bill(self, **kwargs):
        fields = dict(app='ebilling', app_ip='127.0.0.1', app_url='http://x', amount=20,
                      username='u', uuid=str(uuid.uuid4()), device_id='MTR-1', status='PENDING')
        fields.update(kwargs)
        return EBill.objects.create(**fields)


def post_json(client, url, body, **extra):
    return client.post(url, json.dumps(body), content_type='application/json', **extra)


@mock.patch('ebilling.mtn.get_account_holder_name', return_value='KOFI MENSAH')
@mock.patch('ebilling.mtn.get_token', return_value='tok')
class BillTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.app = Application.objects.create(name='ebilling', url='http://x', key='secretkey',
                                              ip_address='127.0.0.1')

    def payload(self, **kw):
        body = {'app': 'ebilling', 'app_key': 'secretkey', 'amount': 10, 'momo_number': '0241234567',
                'device_id': 'MTR-9'}
        body.update(kw)
        return body

    def accepted(self, token, amount, number, ref_id, external_id):
        return {'success': True, 'status_code': 202, 'reference_id': ref_id, 'message': 'ok'}

    @mock.patch('ebilling.mtn.request_to_pay')
    def test_reference_is_sent_as_external_id(self, request_to_pay, _token, lookup):
        request_to_pay.side_effect = self.accepted
        r = post_json(self.client, '/bill/', self.payload())

        self.assertEqual(r.status_code, 200)
        args, kwargs = request_to_pay.call_args
        ref_id = args[3]
        self.assertEqual(kwargs['external_id'], ref_id)
        bill = EBill.objects.get()
        self.assertEqual(bill.uuid, ref_id)
        self.assertTrue(bill.request_sent)

        # payer details: number normalised to 233…, name looked up from MTN
        lookup.assert_called_once_with('tok', '233241234567')
        self.assertEqual((bill.momo_number, bill.momo_name), ('233241234567', 'KOFI MENSAH'))
        self.assertEqual(r.json()['payer'], {'momo_number': '233241234567', 'momo_name': 'KOFI MENSAH'})

    @mock.patch('ebilling.mtn.request_to_pay')
    def test_mtn_token_is_never_returned_or_stored(self, request_to_pay, _token, _lookup):
        request_to_pay.side_effect = self.accepted
        r = post_json(self.client, '/bill/', self.payload())
        self.assertNotIn('tok"', r.content.decode())
        self.assertNotIn('token', r.json()['momo_request'])
        field_names = {f.name for f in EBill._meta.get_fields()}
        self.assertFalse({'token', 'app_key'} & field_names)

    @mock.patch('ebilling.mtn.request_to_pay')
    def test_wrong_or_missing_key_is_rejected(self, request_to_pay, _token, _lookup):
        for body in (self.payload(app_key='nope'), self.payload(app_key=None), self.payload(app='unknown')):
            r = post_json(self.client, '/bill/', body)
            self.assertEqual(r.status_code, 403)
        request_to_pay.assert_not_called()
        self.assertFalse(EBill.objects.exists())

    @mock.patch('ebilling.mtn.request_to_pay')
    def test_caller_must_come_from_registered_ip(self, request_to_pay, _token, _lookup):
        request_to_pay.side_effect = self.accepted
        r = post_json(self.client, '/bill/', self.payload(), REMOTE_ADDR='203.0.113.9')
        self.assertEqual(r.status_code, 403)
        request_to_pay.assert_not_called()

        # a forged X-Forwarded-For entry written by the caller is not trusted
        r = post_json(self.client, '/bill/', self.payload(), REMOTE_ADDR='203.0.113.9',
                      HTTP_X_FORWARDED_FOR='127.0.0.1, 203.0.113.9')
        self.assertEqual(r.status_code, 403)

        # the entry added by our own proxy is
        r = post_json(self.client, '/bill/', self.payload(), REMOTE_ADDR='10.0.0.1',
                      HTTP_X_FORWARDED_FOR='198.51.100.7, 127.0.0.1')
        self.assertEqual(r.status_code, 200)

    @mock.patch('ebilling.mtn.request_to_pay')
    def test_amount_is_validated_and_rounded(self, request_to_pay, _token, _lookup):
        request_to_pay.side_effect = self.accepted
        for bad in (0, -5, 'abc', 'nan', 'inf', None, 10 ** 9):
            self.assertEqual(post_json(self.client, '/bill/', self.payload(amount=bad)).status_code, 400, bad)
        self.assertEqual(post_json(self.client, '/bill/', self.payload(amount=12.345)).status_code, 200)
        self.assertEqual(str(EBill.objects.get().amount), '12.35')

    @mock.patch('ebilling.mtn.request_to_pay')
    def test_payment_is_saved_before_mtn_is_called(self, request_to_pay, _token, _lookup):
        seen = {}

        def fake(token, amount, number, ref_id, external_id):
            seen['saved'] = EBill.objects.filter(uuid=ref_id).exists()
            return self.accepted(token, amount, number, ref_id, external_id)

        request_to_pay.side_effect = fake
        post_json(self.client, '/bill/', self.payload())
        self.assertTrue(seen['saved'])

    @mock.patch('ebilling.mtn.request_to_pay')
    def test_refused_request_is_closed_as_failed(self, request_to_pay, _token, _lookup):
        request_to_pay.return_value = {'success': False, 'status_code': 400, 'message': 'bad'}
        r = post_json(self.client, '/bill/', self.payload())
        self.assertFalse(r.json()['ok'])
        bill = EBill.objects.get()
        self.assertEqual((bill.status, bill.transaction_complete, bill.request_sent), ('FAILED', True, False))

    @mock.patch('ebilling.mtn.request_to_pay')
    def test_uncertain_failure_stays_pending(self, request_to_pay, _token, _lookup):
        request_to_pay.return_value = {'success': False, 'status_code': None, 'message': 'timeout'}
        post_json(self.client, '/bill/', self.payload())
        bill = EBill.objects.get()
        self.assertEqual((bill.status, bill.transaction_complete), ('PENDING', False))

    @mock.patch('ebilling.mtn.request_to_pay')
    def test_rejected_cached_token_is_replaced_once(self, request_to_pay, _token, _lookup):
        request_to_pay.side_effect = [{'success': False, 'status_code': 401, 'message': 'expired'},
                                      {'success': True, 'status_code': 202, 'message': 'ok'}]
        r = post_json(self.client, '/bill/', self.payload())
        self.assertTrue(r.json()['ok'])
        self.assertEqual(request_to_pay.call_count, 2)


@mock.patch('ebilling.mtn.get_token', return_value='tok')
class CallbackTests(PaymentTestCase):
    """The callback body is only used to find the payment; MTN confirms the result."""

    def callback(self, body, method='put', **headers):
        send = getattr(self.client, method)
        return send('/bill/mtn-callback/', json.dumps(body), content_type='application/json', headers=headers)

    def test_callback_is_confirmed_with_mtn(self, _token):
        bill = self.make_bill()
        with mock.patch('ebilling.mtn.check_status',
                        return_value=mtn_status_response('SUCCESSFUL', financial_id='123')) as check:
            r = self.callback({'externalId': bill.uuid, 'status': 'SUCCESSFUL', 'financialTransactionId': 'fake'})
        self.assertEqual(r.status_code, 200)
        check.assert_called_once()
        bill.refresh_from_db()
        self.assertEqual(bill.status, 'SUCCESSFUL')
        self.assertTrue(bill.transaction_complete)
        self.assertEqual(bill.financial_transaction_id, '123')  # MTN's value, not the callback's
        self.assertIsNotNone(bill.request_completed_datetime)
        self.assertEqual(CallBacks.objects.get().method, 'PUT')

    def test_forged_callback_cannot_mark_a_payment_paid(self, _token):
        bill = self.make_bill()
        with mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response('PENDING')):
            self.callback({'externalId': bill.uuid, 'status': 'SUCCESSFUL', 'financialTransactionId': 'fake'})
        bill.refresh_from_db()
        self.assertEqual((bill.status, bill.transaction_complete), ('PENDING', False))
        self.assertEqual(bill.financial_transaction_id, '')

    def test_post_still_accepted_and_get_rejected(self, _token):
        bill = self.make_bill()
        with mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response('FAILED')):
            r = self.callback({'externalId': bill.uuid, 'status': 'FAILED'}, method='post')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get('/bill/mtn-callback/').status_code, 405)

    def test_failure_is_recorded_as_complete(self, _token):
        bill = self.make_bill()
        with mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response('FAILED')):
            self.callback({'externalId': bill.uuid, 'status': 'FAILED'})
        bill.refresh_from_db()
        self.assertEqual(bill.status, 'FAILED')
        self.assertTrue(bill.transaction_complete)
        self.assertIsNotNone(bill.request_completed_datetime)

    def test_reference_in_header_is_matched(self, _token):
        bill = self.make_bill()
        with mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response('SUCCESSFUL')):
            self.callback({'externalId': 'MTR-1', 'status': 'SUCCESSFUL'}, X_Reference_Id=bill.uuid)
        bill.refresh_from_db()
        self.assertEqual(bill.status, 'SUCCESSFUL')

    def test_repeat_callbacks_change_nothing(self, _token):
        bill = self.make_bill()
        with mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response('SUCCESSFUL')) as check:
            self.callback({'externalId': bill.uuid, 'status': 'SUCCESSFUL'})
            r = self.callback({'externalId': bill.uuid, 'status': 'FAILED'})
        bill.refresh_from_db()
        self.assertEqual(bill.status, 'SUCCESSFUL')
        self.assertEqual(r.json()['message'], 'Already processed')
        self.assertEqual(check.call_count, 1)

    def test_payer_number_comes_from_mtn_not_the_callback(self, _token):
        bill = self.make_bill()
        with mock.patch('ebilling.mtn.get_account_holder_name', return_value=''), \
                mock.patch('ebilling.mtn.check_status',
                           return_value=mtn_status_response('SUCCESSFUL', payer='233241112222')):
            self.callback({'externalId': bill.uuid, 'status': 'SUCCESSFUL',
                           'payer': {'partyIdType': 'MSISDN', 'partyId': '233000000000'}})
        bill.refresh_from_db()
        self.assertEqual(bill.momo_number, '233241112222')

    def test_unknown_reference_is_acknowledged_without_calling_mtn(self, _token):
        with mock.patch('ebilling.mtn.check_status') as check:
            r = self.callback({'externalId': str(uuid.uuid4()), 'status': 'SUCCESSFUL'})
        self.assertEqual(r.status_code, 200)
        check.assert_not_called()


@mock.patch('ebilling.mtn.get_token', return_value='tok')
class BillStatusTests(PaymentTestCase):
    def poll(self, ref, **extra):
        return post_json(self.client, '/bill/callback/', dict({'reference_id': ref}, **extra))

    @mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response('FAILED'))
    def test_polling_records_failure(self, check, _token):
        bill = self.make_bill()
        r = self.poll(bill.uuid)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'FAILED')
        self.assertEqual(check.call_args.args, (bill.uuid, 'tok'))  # our token, not one from the caller
        bill.refresh_from_db()
        self.assertEqual(bill.status, 'FAILED')
        self.assertTrue(bill.transaction_complete)

    @mock.patch('ebilling.mtn.check_status')
    def test_caller_supplied_token_is_ignored(self, check, _token):
        check.return_value = mtn_status_response('PENDING')
        bill = self.make_bill()
        self.poll(bill.uuid, token='attacker-token')
        self.assertEqual(check.call_args.args[1], 'tok')

    @mock.patch('ebilling.mtn.check_status')
    def test_final_status_served_from_cache(self, check, _token):
        bill = self.make_bill(status='SUCCESSFUL', transaction_complete=True)
        r = self.poll(bill.uuid)
        self.assertEqual(r.json()['source'], 'cache')
        check.assert_not_called()

    @mock.patch('ebilling.mtn.check_status')
    def test_unknown_reference_is_not_proxied_to_mtn(self, check, _token):
        r = self.poll(str(uuid.uuid4()))
        self.assertEqual(r.status_code, 404)
        check.assert_not_called()
        self.assertEqual(self.poll('').status_code, 400)


class DashboardPayTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        Application.objects.create(name='ebilling', url='http://x', key='secretkey', ip_address='203.0.113.1')

    def body(self, **kw):
        return dict({'app': 'ebilling', 'amount': 5, 'momo_number': '0241234567', 'device_id': 'M1'}, **kw)

    def test_requires_staff_login(self):
        self.assertEqual(post_json(self.client, '/dashboard/pay/start/', self.body()).status_code, 302)
        self.assertEqual(post_json(self.client, '/dashboard/pay/status/', {'reference_id': 'x'}).status_code, 302)

    @mock.patch('ebilling.mtn.get_account_holder_name', return_value='')
    @mock.patch('ebilling.mtn.request_to_pay', return_value={'success': True, 'status_code': 202, 'message': 'ok'})
    @mock.patch('ebilling.mtn.get_token', return_value='tok')
    def test_staff_can_start_and_poll_without_api_key_or_ip_match(self, _token, _pay, _lookup):
        self.client.force_login(User.objects.create_user('staff', password='pw', is_staff=True))
        r = post_json(self.client, '/dashboard/pay/start/', self.body())  # test client IP is 127.0.0.1
        self.assertTrue(r.json()['ok'])
        self.assertNotIn('token', r.json()['momo_request'])
        bill = EBill.objects.get()
        self.assertEqual(bill.username, 'staff')
        with mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response('SUCCESSFUL')):
            r = post_json(self.client, '/dashboard/pay/status/', {'reference_id': bill.uuid})
        self.assertEqual(r.json()['status'], 'SUCCESSFUL')

    def test_pay_page_does_not_expose_app_keys(self):
        self.client.force_login(User.objects.create_user('staff', password='pw', is_staff=True))
        self.assertNotContains(self.client.get('/dashboard/pay/'), 'secretkey')


@mock.patch('ebilling.mtn.get_token', return_value='tok')
class DashboardCheckTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_user('staff', password='pw', is_staff=True))

    @mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response('SUCCESSFUL'))
    def test_check_button_records_result(self, _check, _token):
        bill = self.make_bill()
        r = self.client.post(f'/dashboard/transactions/{bill.pk}/check/', follow=True)
        self.assertContains(r, 'MTN reports SUCCESSFUL')
        bill.refresh_from_db()
        self.assertEqual(bill.status, 'SUCCESSFUL')

    @mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response(None, code=404))
    def test_check_button_reports_not_found(self, _check, _token):
        bill = self.make_bill()
        r = self.client.post(f'/dashboard/transactions/{bill.pk}/check/', follow=True)
        self.assertContains(r, 'MTN has no record')
        bill.refresh_from_db()
        self.assertEqual(bill.status, 'PENDING')

    def test_check_requires_post(self, _token):
        bill = self.make_bill()
        self.assertEqual(self.client.get(f'/dashboard/transactions/{bill.pk}/check/').status_code, 405)


@mock.patch('ebilling.management.commands.recheck_pending.time.sleep')
@mock.patch('ebilling.mtn.get_token', return_value='tok')
class RecheckPendingTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        old = timezone.now() - timedelta(hours=1)
        self.ok, self.missing, self.fresh = (self.make_bill(), self.make_bill(), self.make_bill())
        EBill.objects.filter(pk__in=[self.ok.pk, self.missing.pk]).update(request_datetime=old)

    def fake_mtn(self, ref_id, token):
        return mtn_status_response('SUCCESSFUL') if ref_id == self.ok.uuid else mtn_status_response(None, code=404)

    def run_command(self, *args):
        out = StringIO()
        with mock.patch('ebilling.mtn.check_status', side_effect=self.fake_mtn) as check:
            call_command('recheck_pending', *args, stdout=out)
        return out.getvalue(), check

    def test_dry_run_saves_nothing(self, *_):
        output, _check = self.run_command('--dry-run', '--fail-not-found')
        self.assertIn('SUCCESSFUL: 1', output)
        self.assertEqual(EBill.objects.filter(status='PENDING').count(), 3)

    def test_records_results_and_skips_recent(self, *_):
        output, check = self.run_command()
        self.assertEqual(check.call_count, 2)  # the fresh request is left alone
        self.assertEqual(EBill.objects.get(pk=self.ok.pk).status, 'SUCCESSFUL')
        self.assertEqual(EBill.objects.get(pk=self.missing.pk).status, 'PENDING')
        self.assertIn('--fail-not-found', output)

    def test_fail_not_found(self, *_):
        self.run_command('--fail-not-found')
        missing = EBill.objects.get(pk=self.missing.pk)
        self.assertEqual(missing.status, 'FAILED')
        self.assertTrue(missing.transaction_complete)


@mock.patch('ebilling.dashboard.transactions.CHECK_BATCH_SIZE', 2)
class BulkCheckTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_user('staff', password='pw', is_staff=True))
        self.pending = [self.make_bill(app='ebilling') for _ in range(3)]
        self.other_app = self.make_bill(app='other')
        self.done = self.make_bill(status='SUCCESSFUL', transaction_complete=True)

    def run_all(self, **extra):
        """Drive the endpoint batch by batch the way the page's script does."""
        after, batches, totals = 0, 0, {}
        while True:
            r = self.client.post('/dashboard/transactions/check/', dict(extra, after=after))
            self.assertEqual(r.status_code, 200)
            data = r.json()
            batches += 1
            for k, n in data['counts'].items():
                totals[k] = totals.get(k, 0) + n
            after = data['after']
            if data['done']:
                return batches, totals

    @mock.patch('ebilling.mtn.get_token', return_value='tok')
    def test_checks_only_filtered_unfinished_in_batches(self, _token):
        first = self.pending[0].uuid
        fake = lambda ref, tok: mtn_status_response('SUCCESSFUL' if ref == first else 'PENDING')
        with mock.patch('ebilling.mtn.check_status', side_effect=fake) as check:
            batches, totals = self.run_all(status='PENDING', app='ebilling')
        self.assertEqual(check.call_count, 3)  # other app and completed record skipped
        self.assertEqual(batches, 2)  # batch size 2 -> 2 + 1
        self.assertEqual(totals, {'successful': 1, 'failed': 0, 'pending': 2, 'not_found': 0, 'error': 0})
        self.assertEqual(EBill.objects.get(uuid=first).status, 'SUCCESSFUL')
        self.assertEqual(EBill.objects.get(pk=self.other_app.pk).status, 'PENDING')

    @mock.patch('ebilling.mtn.get_token', return_value='tok')
    @mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response(None, code=404))
    def test_fail_not_found_option(self, _check, _token):
        _, totals = self.run_all(app='ebilling', fail_not_found='1')
        self.assertEqual(totals['failed'], 3)
        self.assertFalse(EBill.objects.filter(app='ebilling', status='PENDING').exists())

    @mock.patch('ebilling.mtn.get_token', return_value=None)
    def test_reports_token_failure(self, _token):
        r = self.client.post('/dashboard/transactions/check/', {'after': 0})
        self.assertEqual(r.status_code, 503)
        self.assertIn('access token', r.json()['error'])

    def test_button_shows_unfinished_count(self):
        r = self.client.get('/dashboard/transactions/?status=PENDING')
        self.assertContains(r, 'Check 4 with MTN')
        r = self.client.get('/dashboard/transactions/?status=SUCCESSFUL')
        self.assertNotContains(r, 'with MTN')


class PayerBackfillTests(PaymentTestCase):
    @mock.patch('ebilling.mtn.get_account_holder_name', return_value='AMA SERWAA')
    @mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response('SUCCESSFUL', payer='233249998888'))
    def test_status_check_backfills_number_and_name(self, _check, lookup):
        from .payments import check_with_mtn
        bill = self.make_bill()
        check_with_mtn(bill, 'tok')
        bill.refresh_from_db()
        self.assertEqual((bill.momo_number, bill.momo_name), ('233249998888', 'AMA SERWAA'))
        lookup.assert_called_once_with('tok', '233249998888')

    @mock.patch('ebilling.mtn.get_account_holder_name')
    @mock.patch('ebilling.mtn.check_status', return_value=mtn_status_response('PENDING', payer='233200000000'))
    def test_existing_payer_details_are_kept(self, _check, lookup):
        from .payments import check_with_mtn
        bill = self.make_bill(momo_number='233241234567', momo_name='KOFI MENSAH')
        check_with_mtn(bill, 'tok')
        bill.refresh_from_db()
        self.assertEqual((bill.momo_number, bill.momo_name), ('233241234567', 'KOFI MENSAH'))
        lookup.assert_not_called()

    def test_dashboard_shows_and_searches_payer(self):
        self.client.force_login(User.objects.create_user('staff', password='pw', is_staff=True))
        self.make_bill(momo_number='233241234567', momo_name='KOFI MENSAH')
        self.make_bill(momo_number='233559990000', momo_name='YAW BOATENG')
        r = self.client.get('/dashboard/transactions/?q=kofi')
        self.assertContains(r, 'KOFI MENSAH')
        self.assertContains(r, '233241234567')
        self.assertNotContains(r, 'YAW BOATENG')
        csv_text = self.client.get('/dashboard/transactions/?export=csv').content.decode()
        self.assertIn('Payer name,Payer number', csv_text)
        self.assertIn('KOFI MENSAH,233241234567', csv_text)


class AccountHolderLookupTests(TestCase):
    def fake_response(self, status_code, body):
        resp = mock.Mock(status_code=status_code)
        resp.json.return_value = body
        return resp

    @mock.patch('ebilling.mtn.requests.get')
    def test_name_from_mtn(self, get):
        from . import mtn as this
        get.return_value = self.fake_response(200, {'given_name': 'Kofi', 'family_name': 'Mensah'})
        self.assertEqual(this.get_account_holder_name('tok', '233241234567'), 'Kofi Mensah')
        self.assertIn('/accountholder/msisdn/233241234567/basicuserinfo', get.call_args.args[0])
        get.return_value = self.fake_response(200, {'name': 'KOFI MENSAH', 'given_name': 'Kofi'})
        self.assertEqual(this.get_account_holder_name('tok', '233241234567'), 'KOFI MENSAH')

    @mock.patch('ebilling.mtn.requests.get')
    def test_lookup_failures_return_blank(self, get):
        from . import mtn as this
        get.return_value = self.fake_response(404, {})
        self.assertEqual(this.get_account_holder_name('tok', '233241234567'), '')
        get.side_effect = this.requests.exceptions.Timeout()
        self.assertEqual(this.get_account_holder_name('tok', '233241234567'), '')


class ReportTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_user('staff', password='pw', is_staff=True))
        self.make_bill(amount='40.00', status='SUCCESSFUL', transaction_complete=True)
        self.make_bill(amount='10.55', status='SUCCESSFUL', transaction_complete=True)
        self.make_bill(amount='99.00', status='FAILED', transaction_complete=True)
        self.make_bill(amount='5.00')  # pending

    def test_deduction_is_one_percent_of_successful_payments_only(self):
        r = self.client.get('/dashboard/reports/?period=yearly')
        totals = r.context['totals']
        self.assertEqual(str(totals['collected']), '50.55')
        self.assertEqual(str(totals['fee']), '0.51')   # 0.40 + 0.11, each day rounded half-up
        self.assertEqual(str(totals['net']), '50.04')
        self.assertContains(r, 'Deduction (1%)')
        self.assertContains(r, 'Rate (%)')

    def test_dashboard_collected_card_shows_deduction(self):
        r = self.client.get('/dashboard/')
        self.assertContains(r, 'From successful payments')
        self.assertContains(r, 'Less 1%')

    def test_pdf_and_csv_exports(self):
        r = self.client.get('/dashboard/reports/?period=yearly&export=pdf')
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF'))
        csv_text = self.client.get('/dashboard/reports/?period=yearly&export=csv').content.decode()
        self.assertIn('Deduction (1%)', csv_text)
        self.assertIn('Total', csv_text)

    def test_empty_period_exports_without_error(self):
        r = self.client.get('/dashboard/reports/?period=custom&from=2001-01-01&to=2001-01-03&export=pdf')
        self.assertTrue(r.content.startswith(b'%PDF'))


class PagerTests(PaymentTestCase):
    def test_three_numbers_around_current_page_with_arrows(self):
        self.client.force_login(User.objects.create_user('staff', password='pw', is_staff=True))
        for _ in range(260):
            self.make_bill()
        html = self.client.get('/dashboard/transactions/?page=3&status=PENDING').content.decode()
        for expected in ('page=1"', 'page=2"', 'page=4"', 'page=6"', 'status=PENDING&page=4'):
            self.assertIn(expected, html)
        self.assertEqual(html.count('pager-btn active'), 1)


class FailureReasonTests(PaymentTestCase):
    def test_extract_reason_handles_both_mtn_formats(self):
        from .payments import extract_reason, failure_text
        self.assertEqual(extract_reason({'reason': 'EXPIRED'}), 'EXPIRED')
        self.assertEqual(extract_reason({'reason': {'code': 'NOT_ENOUGH_FUNDS', 'message': 'x'}}), 'NOT_ENOUGH_FUNDS')
        self.assertEqual(extract_reason({'status': 'FAILED'}), '')
        self.assertIn('PIN not entered', failure_text('EXPIRED'))
        self.assertEqual(failure_text('SOMETHING_NEW'), 'Something new')
        self.assertEqual(failure_text(''), '')

    @mock.patch('ebilling.mtn.get_token', return_value='tok')
    def test_reason_is_recorded_from_mtn_and_shown(self, _token):
        self.client.force_login(User.objects.create_user('staff', password='pw', is_staff=True))
        bill = self.make_bill()
        body = json.dumps({'status': 'FAILED', 'reason': 'EXPIRED'})
        with mock.patch('ebilling.mtn.check_status', return_value={
                'success': True, 'status_code': 200, 'raw_response_text': body}):
            r = post_json(self.client, '/bill/callback/', {'reference_id': bill.uuid})
        self.assertEqual(r.json()['reason_code'], 'EXPIRED')
        self.assertIn('PIN not entered', r.json()['reason'])
        bill.refresh_from_db()
        self.assertEqual(bill.failure_reason, 'EXPIRED')
        self.assertContains(self.client.get(f'/dashboard/transactions/{bill.pk}/'), 'Reason for failure')
        self.assertContains(self.client.get('/dashboard/transactions/'), 'PIN not entered')
        self.assertIn('PIN not entered', self.client.get('/dashboard/transactions/?export=csv').content.decode())

    @mock.patch('ebilling.mtn.get_token', return_value='tok')
    def test_backfill_command_fills_missing_reasons_only(self, _token):
        missing = self.make_bill(status='FAILED', transaction_complete=True)
        have = self.make_bill(status='FAILED', transaction_complete=True, failure_reason='EXPIRED')
        body = json.dumps({'status': 'FAILED', 'reason': {'code': 'APPROVAL_REJECTED'}})
        with mock.patch('ebilling.mtn.check_status',
                        return_value={'success': True, 'raw_response_text': body}) as check:
            call_command('recheck_pending', '--backfill-reasons', '--pause', '0', stdout=StringIO())
        self.assertEqual(check.call_count, 1)
        missing.refresh_from_db(); have.refresh_from_db()
        self.assertEqual(missing.failure_reason, 'APPROVAL_REJECTED')
        self.assertEqual(have.failure_reason, 'EXPIRED')


class ApiProtectionTests(PaymentTestCase):
    def test_bill_is_rate_limited_per_ip(self):
        statuses = [post_json(self.client, '/bill/', {}).status_code for _ in range(32)]
        self.assertEqual(statuses[:30], [403] * 30)   # rejected (no credentials) but not yet throttled
        self.assertEqual(statuses[30:], [429, 429])
        # another address has its own allowance
        self.assertEqual(post_json(self.client, '/bill/', {}, REMOTE_ADDR='203.0.113.5').status_code, 403)

    @mock.patch('ebilling.mtn.get_account_holder_name', return_value='')
    @mock.patch('ebilling.mtn.request_to_pay',
                return_value={'success': True, 'status_code': 202, 'message': 'ok'})
    @mock.patch('ebilling.mtn.get_token', return_value='tok')
    def test_repeated_request_sends_only_one_prompt(self, _token, request_to_pay, _lookup):
        Application.objects.create(name='ebilling', url='http://x', key='k', ip_address='127.0.0.1')
        body = {'app': 'ebilling', 'app_key': 'k', 'amount': 20, 'momo_number': '0241234567', 'device_id': 'M1'}
        first = post_json(self.client, '/bill/', body).json()
        second = post_json(self.client, '/bill/', body).json()
        self.assertEqual(request_to_pay.call_count, 1)
        self.assertEqual(EBill.objects.count(), 1)
        self.assertEqual(first['momo_request']['request_reference_id'], second['momo_request']['request_reference_id'])
        self.assertTrue(second['duplicate'])

        # a different amount, or the same request once the first is no longer pending, is a new payment
        post_json(self.client, '/bill/', dict(body, amount=25))
        self.assertEqual(EBill.objects.count(), 2)
        EBill.objects.update(status='FAILED')
        post_json(self.client, '/bill/', body)
        self.assertEqual(EBill.objects.count(), 3)


@mock.patch('ebilling.mtn.get_token', return_value='tok')
class CallbackOutcomeTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_user('staff', password='pw', is_staff=True))

    def send(self, body, mtn_status=None, **extra):
        with mock.patch('ebilling.mtn.check_status', return_value=mtn_status or mtn_status_response('PENDING')):
            self.client.put('/bill/mtn-callback/', json.dumps(body), content_type='application/json', **extra)
        return CallBacks.objects.latest('pk')

    def test_confirmed_callback_is_linked_and_recorded(self, _token):
        bill = self.make_bill()
        log = self.send({'externalId': bill.uuid, 'status': 'SUCCESSFUL'}, mtn_status_response('SUCCESSFUL'))
        self.assertEqual((log.ebill, log.reference, log.outcome), (bill, bill.uuid, 'processed'))
        self.assertEqual((log.claimed_status, log.verified_status), ('SUCCESSFUL', 'SUCCESSFUL'))

    def test_repeat_callback_is_already_recorded(self, _token):
        bill = self.make_bill(status='SUCCESSFUL', transaction_complete=True)
        self.assertEqual(self.send({'externalId': bill.uuid, 'status': 'SUCCESSFUL'}).outcome, 'already_processed')

    def test_claim_mtn_does_not_confirm_is_flagged(self, _token):
        bill = self.make_bill()
        log = self.send({'externalId': bill.uuid, 'status': 'SUCCESSFUL'}, mtn_status_response('PENDING'))
        self.assertEqual(log.outcome, 'unconfirmed')
        bill.refresh_from_db()
        self.assertFalse(bill.transaction_complete)

    def test_claim_that_contradicts_mtn_is_a_mismatch_and_mtn_wins(self, _token):
        bill = self.make_bill()
        log = self.send({'externalId': bill.uuid, 'status': 'SUCCESSFUL'}, mtn_status_response('FAILED'))
        self.assertEqual(log.outcome, 'mismatch')
        bill.refresh_from_db()
        self.assertEqual(bill.status, 'FAILED')

    def test_pending_unknown_and_invalid_callbacks(self, _token):
        bill = self.make_bill()
        self.assertEqual(self.send({'externalId': bill.uuid, 'status': 'PENDING'}).outcome, 'pending')
        ghost = str(uuid.uuid4())
        log = self.send({'externalId': ghost, 'status': 'SUCCESSFUL'})
        self.assertEqual((log.outcome, log.reference, log.ebill), ('unknown_reference', ghost, None))
        self.client.put('/bill/mtn-callback/', 'not json', content_type='application/json')
        self.assertEqual(CallBacks.objects.latest('pk').outcome, 'invalid')

    def test_mtn_unreachable_is_unverified(self, _token):
        bill = self.make_bill()
        log = self.send({'externalId': bill.uuid, 'status': 'SUCCESSFUL'},
                        {'success': False, 'status_code': None, 'message': 'timeout'})
        self.assertEqual(log.outcome, 'unverified')

    def test_pages_show_links_outcomes_and_filters(self, _token):
        bill = self.make_bill()
        self.send({'externalId': bill.uuid, 'status': 'SUCCESSFUL'}, mtn_status_response('SUCCESSFUL'))
        self.send({'externalId': str(uuid.uuid4()), 'status': 'SUCCESSFUL'})
        listing = self.client.get('/dashboard/callbacks/')
        self.assertContains(listing, 'Recorded')
        self.assertContains(listing, f'/dashboard/transactions/{bill.pk}/')
        self.assertContains(listing, 'No matching payment')
        attention = self.client.get('/dashboard/callbacks/?outcome=attention')
        self.assertEqual(len(attention.context['page']), 1)
        self.assertContains(attention, 'Unknown reference')
        self.assertEqual(len(self.client.get('/dashboard/callbacks/?q=' + bill.uuid[:8]).context['page']), 1)
        # the payment's own page lists its callbacks, and a callback links back to the payment
        self.assertContains(self.client.get(f'/dashboard/transactions/{bill.pk}/'), 'Callbacks from MTN')
        log = bill.callbacks.get()
        self.assertContains(self.client.get(f'/dashboard/callbacks/{log.pk}/'), 'MTN confirmed the result')

    def test_old_callbacks_without_outcome_still_display(self, _token):
        old = CallBacks.objects.create(url='u', method='PUT', contents='{}', ip_address='127.0.0.1')
        self.assertContains(self.client.get('/dashboard/callbacks/'), 'No matching payment', count=0)
        self.assertEqual(self.client.get(f'/dashboard/callbacks/{old.pk}/').status_code, 200)

    def test_health_warns_about_payments_with_no_callback(self, _token):
        from .dashboard.common import callback_health
        long_ago = timezone.now() - timedelta(hours=2)
        waiting = self.make_bill(request_sent=True)
        has_callback = self.make_bill(request_sent=True)
        self.make_bill(request_sent=True)  # too recent to worry about
        EBill.objects.filter(pk__in=[waiting.pk, has_callback.pk]).update(request_datetime=long_ago)
        CallBacks.objects.create(url='u', method='PUT', contents='{}', ip_address='127.0.0.1', ebill=has_callback)
        health = callback_health()
        self.assertEqual(health['stale_pending'], 1)
        self.assertEqual(health['last_day'], 1)
        self.assertContains(self.client.get('/dashboard/'), 'with no callback from MTN')
        self.assertContains(self.client.get('/dashboard/callbacks/'), 'Last callback received')


class CallbackBackfillTests(PaymentTestCase):
    def test_old_callbacks_are_linked_to_their_payments(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module('ebilling.migrations.0009_backfill_callback_links')
        bill = self.make_bill()
        old = CallBacks.objects.create(url='u', method='PUT', ip_address='127.0.0.1',
                                       contents=json.dumps({'externalId': bill.uuid, 'status': 'successful'}))
        junk = CallBacks.objects.create(url='u', method='PUT', ip_address='127.0.0.1', contents='not json')
        migration.link_existing_callbacks(apps, None)
        old.refresh_from_db(); junk.refresh_from_db()
        self.assertEqual((old.ebill, old.reference, old.claimed_status, old.outcome), (bill, bill.uuid, 'SUCCESSFUL', ''))
        self.assertEqual((junk.ebill, junk.reference), (None, ''))


class ApplicationAddressTests(PaymentTestCase):
    def app(self, ips):
        return Application(name='a', url='u', key='k', ip_address=ips)

    def test_several_addresses_and_ranges_are_allowed(self):
        app = self.app('10.0.4.170, 10.0.4.218 10.0.5.0/24, 2001:db8::/32')
        for ok in ('10.0.4.170', '10.0.4.218', '10.0.5.156', '10.0.5.1', '2001:db8::1'):
            self.assertTrue(app.allows_ip(ok), ok)
        for bad in ('10.0.4.171', '10.0.6.1', '203.0.113.9', '', 'not-an-ip', None):
            self.assertFalse(app.allows_ip(bad), bad)

    def test_form_checks_the_list(self):
        from .forms import ApplicationForm
        data = {'name': 'x', 'url': 'http://x', 'key': ''}
        good = ApplicationForm(dict(data, ip_address='10.0.4.170, 10.0.5.0/24'))
        self.assertTrue(good.is_valid(), good.errors)
        self.assertEqual(len(good.cleaned_data['key']), 32)   # generated keys are long
        for bad in ('', 'nonsense', '10.0.4.999', '10.0.4.0/99'):
            self.assertFalse(ApplicationForm(dict(data, ip_address=bad)).is_valid(), bad)

    @mock.patch('ebilling.mtn.get_account_holder_name', return_value='')
    @mock.patch('ebilling.mtn.request_to_pay', return_value={'success': True, 'status_code': 202, 'message': 'ok'})
    @mock.patch('ebilling.mtn.get_token', return_value='tok')
    def test_bill_accepts_any_registered_server(self, _token, _pay, _lookup):
        Application.objects.create(name='ebilling', url='u', key='k', ip_address='10.0.4.170, 10.0.5.0/24')
        body = {'app': 'ebilling', 'app_key': 'k', 'amount': 5, 'momo_number': '0241234567', 'device_id': 'M1'}
        for i, (ip, expected) in enumerate((('10.0.4.170', 200), ('10.0.5.156', 200), ('10.0.4.218', 403))):
            r = post_json(self.client, '/bill/', dict(body, device_id=f'M{i}'), REMOTE_ADDR=ip)
            self.assertEqual(r.status_code, expected, ip)


class BackupDbTests(PaymentTestCase):
    def run_backup(self, dest, **opts):
        out = StringIO()
        call_command('backup_db', dest=str(dest), stdout=out, **opts)
        return out.getvalue()

    def test_only_the_newest_backups_are_kept(self):
        import os
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            old = []
            for i in range(4):
                f = Path(tmp) / f'ibsbill-2020010{i}-000000.sqlite3.gz'
                f.write_bytes(b'x')
                os.utime(f, (1_600_000_000 + i, 1_600_000_000 + i))
                old.append(f)
            (Path(tmp) / 'notes.txt').write_text('not a backup')
            self.run_backup(tmp, keep=2)
            left = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(len(left), 3)                          # 2 backups + notes.txt
            self.assertIn('notes.txt', left)
            self.assertFalse(old[0].exists())
            self.assertFalse(old[1].exists())
            self.assertFalse(old[2].exists())                       # the new backup outranks them all

    def test_keep_must_be_positive(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('backup_db', keep=0, stdout=StringIO())


class BackupDbDataTests(TransactionTestCase):
    """Needs real commits (no wrapping test transaction), like a live run of the command."""

    def test_sqlite_backup_is_a_valid_copy(self):
        import gzip
        import sqlite3
        import tempfile
        from pathlib import Path
        Application.objects.create(name='backed-up-app', url='u', key='k', ip_address='10.0.0.1')
        with tempfile.TemporaryDirectory() as tmp:
            call_command('backup_db', dest=tmp, stdout=StringIO())
            files = list(Path(tmp).glob('ibsbill-*.sqlite3.gz'))
            self.assertEqual(len(files), 1)
            restored = Path(tmp) / 'restored.sqlite3'
            restored.write_bytes(gzip.decompress(files[0].read_bytes()))
            conn = sqlite3.connect(str(restored))
            try:
                names = [r[0] for r in conn.execute('select name from ebilling_application')]
            finally:
                conn.close()
            self.assertEqual(names, ['backed-up-app'])
            self.assertFalse(list(Path(tmp).glob('ibsbill-*.sqlite3')))   # no uncompressed copy left behind
