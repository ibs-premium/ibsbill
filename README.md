# eBilling MoMo Gateway (ibsbill)

A Django service that collects **MTN Mobile Money** payments for IBS eBilling. eBilling (and any other registered application) asks the gateway to charge a customer's MoMo wallet; the gateway sends the request-to-pay to MTN, records the result, and lets eBilling poll for the outcome.

It also includes a staff **dashboard** for taking payments, following up stuck ones, and reporting.

- Staff guide: [docs/STAFF_MANUAL.md](docs/STAFF_MANUAL.md) (edit this one, then run `python docs/build_manual.py` to rebuild the Word and PDF copies), also as
  [Word](docs/eBilling_MoMo_Staff_Manual.docx) and [PDF](docs/eBilling_MoMo_Staff_Manual.pdf) for sharing and printing

## Features

- **Payment API** for eBilling: start a payment, poll its status, receive MTN's callback.
- **Staff dashboard** at `/dashboard/` (staff accounts only):
  - Overview with totals, success rate and a 14-day chart.
  - MoMo Payment page to send a payment prompt and follow it live.
  - Transactions list with search, filters, CSV export and a detail page per payment.
  - Check one payment, or every filtered unfinished payment, with MTN.
  - Reports: monthly, quarterly, half-yearly, yearly or a chosen date range, compared with the previous period; CSV export and print.
  - Callback logs: every raw MTN callback, linked to its payment and marked with what the gateway made of it (recorded, disagrees with MTN, unknown reference, …), plus a health strip showing when MTN last called back and a warning when payments are pending with no callback.
  - Application management.
- Payer **MoMo number and registered name** recorded on every payment.
- `recheck_pending` management command to settle stuck payments from the server.

## Requirements

- Python 3.10+
- Packages in `requirements.txt` (Django, django-cors-headers, python-decouple, requests, pytz)
- MTN MoMo **Collection** API credentials (subscription key, API user, API key)

> `requirements.txt` pins Django 5.2.1; the code and tests have also been run on Django 6.0.

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root (it is git-ignored):

```ini
SECRET_KEY=<long random string>
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# MTN MoMo Collection API
MTN_SUBSCRIPTION_KEY=<subscription key>
MTN_API_USER=<api user uuid>
MTN_API_KEY=<api key>
MTN_CALLBACK_URL=https://<your-domain>/bill/mtn-callback/
MTN_CURRENCY=GHS
MTN_ENVIRONMENT=mtnghana

# Outgoing email (password reset)
EMAIL_HOST_USER=<gmail address>
EMAIL_HOST_PASSWORD=<app password>
```

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Django secret key. Generate with `python -c "import secrets; print(secrets.token_urlsafe(50))"`. |
| `DEBUG` | `True` for development only. |
| `ALLOWED_HOSTS` | Comma-separated host names the server answers to. |
| `MTN_SUBSCRIPTION_KEY`, `MTN_API_USER`, `MTN_API_KEY` | MTN Collection credentials. |
| `MTN_CALLBACK_URL` | Public **HTTPS** URL of `/bill/mtn-callback/`. MTN sends payment results here. |
| `MTN_CURRENCY` | Currency code sent to MTN and shown in the dashboard (e.g. `GHS`). |
| `MTN_ENVIRONMENT` | MTN target environment (e.g. `mtnghana`, or `sandbox` for testing). |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | SMTP login (Gmail) for outgoing mail. |
| `TRUSTED_PROXY_COUNT` | Reverse proxies (nginx) in front of the app; default `1`, use `0` with none. Used to read the caller's real IP. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated eBilling site origins (`https://host`, no path, no trailing slash) allowed to call `/bill/` from a browser. eBilling calls from the customer's browser, so a site not listed here cannot take payments. |
| `ENFORCE_APP_IP` | Default `True`, but set it `False` while eBilling calls from customers' browsers (the caller's IP is then the customer's own). When on: `/bill/` only accepts an application from the IP addresses registered for it (a list of addresses and ranges such as `10.0.4.170, 10.0.5.0/24`). |
| `COLLECTION_FEE_PERCENT` | Deduction shown in the reports (default `1`). |
| `DB_ENGINE` | `sqlite` (default) or `postgresql`. For PostgreSQL also set `DB_NAME`, `DB_USER`, `DB_PASSWORD` (and `DB_HOST`, `DB_PORT`) and run `pip install "psycopg[binary]"`. Use PostgreSQL in production. |
| `CACHE_DIR` | Where the shared cache lives (default `./cache`). Holds the MTN token and the rate-limit counters, so all gunicorn workers must be able to read and write it. |

Then create the database tables and a first staff user:

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/dashboard/> and sign in. `/` redirects to the dashboard.

Register each calling system (e.g. `diamond-ebilling`) under **Applications** before it calls the API, and add its site to `CORS_ALLOWED_ORIGINS`. See section 13 of the staff manual for the full steps, and section 14 for updating a live server.

## How a payment works

```
eBilling ──POST /bill/──────────────▶ gateway ──request-to-pay──▶ MTN ──prompt──▶ customer's phone
                                        │  saves EBill (PENDING)                      │ approves / declines
eBilling ──POST /bill/callback/ ─────▶ │ ◀──── PUT /bill/mtn-callback/ ──────────────┘ (MTN callback)
          (polls until final)           │ ──GET requesttopay/{ref}──▶ MTN  (if no callback yet)
```

1. `/bill/` checks the application's key and IP address, validates the number, looks up the payer's MoMo name, and sends MTN a request-to-pay. Our reference (a UUID) is sent as both `X-Reference-Id` and `externalId`, so MTN's callback can be matched to the payment.
2. MTN prompts the customer. When they answer, MTN calls `MTN_CALLBACK_URL`. The callback is logged in **Callback Logs** and the payment is marked `SUCCESSFUL` or `FAILED`.
3. eBilling polls `/bill/callback/`. If the result is already stored it is returned from cache; otherwise the gateway asks MTN and stores any final result.
4. Stuck payments can be checked from the dashboard or with `recheck_pending`.

All four paths record the outcome through `ebilling/payments.py:apply_final_status`, so a payment is completed once and never changed afterwards.

## API

All endpoints take and return JSON. They are CSRF-exempt and meant for server-to-server calls.

### `POST /bill/`: start a payment

```json
{
  "app": "diamond-ebilling",
  "app_key": "<application key>",
  "amount": 20.00,
  "momo_number": "0241234567",
  "device_id": "MTR-001234",
  "username": "ROOM A101"
}
```

`app_key` must match the key registered for the application, and the request must come from the IP address registered for it. `amount` is rounded to 2 decimal places. `momo_number` is 10 digits starting with `0`, or 12 digits starting with `233`; it must be an MTN number.

Success:

```json
{
  "ok": true,
  "momo_request": {
    "success": true,
    "request_reference_id": "3f2b8c1e-7d4a-4e9b-a6c2-1b5d9e0f7a43"
  },
  "payer": { "momo_number": "233241234567", "momo_name": "KOFI MENSAH" }
}
```

MTN refused the request: `{"ok": false, "momo_request": {"success": false, "message": "..."}}` (the payment is recorded as `FAILED` when MTN refused it outright, and left `PENDING` when the outcome is uncertain, e.g. a timeout).
Errors: `403` wrong app name, key or IP (all give the same answer), `400` invalid JSON/amount/number, `503` no MTN token.

### `POST /bill/callback/`: poll a payment

```json
{ "reference_id": "3f2b8c1e-..." }
```

When the result is stored: `{"status": "SUCCESSFUL", "source": "cache", "raw_response_text": "{...}"}`.
Unknown references get `404`. The gateway uses its own MTN credentials, so no token is needed (a `token` field is ignored). Otherwise it asks MTN and returns MTN's answer (with `raw_response_text` containing MTN's JSON, whose `status` is `PENDING`, `SUCCESSFUL` or `FAILED`).

### `PUT /bill/mtn-callback/`: MTN's result callback

Called by MTN, not by eBilling. Accepts `PUT` and `POST`. Every call is stored in `CallBacks`. The payment is found by `externalId` (also `referenceId` or the `X-Reference-Id` header). The body is not trusted: the result is confirmed by asking MTN, so a forged callback cannot mark a payment paid. Repeat callbacks change nothing. Any valid JSON gets `200` (unknown references included) so MTN doesn't retry; invalid JSON gets `400`.

## Keeping payments settled

Payments left `PENDING` (customer never answered, callback lost) are settled by `recheck_pending`. Run it on a schedule rather than by hand. With systemd, copy the two files in `deploy/` to `/etc/systemd/system/`, adjust the paths and user, then:

```bash
sudo systemctl enable --now ibsbill-recheck.timer
```

It checks anything still pending after 10 minutes, every 15 minutes. Add `--fail-not-found` only once you are sure the MTN credentials and environment are right.

The API endpoints are rate limited per IP (`/bill/` 30 a minute, the others 240), and a repeated identical request within a minute returns the first payment instead of sending a second prompt to the customer.

## Dashboard pages

| URL | Page |
|---|---|
| `/dashboard/` | Overview |
| `/dashboard/pay/` | MoMo Payment |
| `/dashboard/transactions/` | Transactions (`?q=&status=&app=&from=&to=`, `&export=csv`) |
| `/dashboard/transactions/<id>/` | Transaction detail |
| `/dashboard/reports/` | Reports (`?period=monthly|quarterly|half|yearly|custom`) |
| `/dashboard/callbacks/` | Callback logs |
| `/dashboard/applications/` | Applications |
| `/admin/` | Django admin (user accounts) |

Only active users with **Staff status** can sign in.

## Management command

```bash
python manage.py recheck_pending --dry-run          # check with MTN, save nothing
python manage.py recheck_pending                    # record final results
python manage.py recheck_pending --fail-not-found   # also fail references MTN has no record of
```

Options: `--older-than MINUTES` (default 10), `--limit N`, `--pause SECONDS` (default 0.2). Safe to run on a schedule.

## Tests

```bash
python manage.py test ebilling
```

MTN is mocked throughout; the tests never call the real API.

## Project layout

```
ibsbill/                 Django project (settings, root URLs)
ebilling/
  views.py               Payment API: /bill/, /bill/callback/, /bill/mtn-callback/
  mtn.py                 MTN API client (token, request-to-pay, status, payer name), with timeouts
  ratelimit.py           Per-IP rate limiting for the API endpoints
  report_pdf.py          PDF export of the reports
  dashboard/             Staff dashboard views: overview, transactions, applications, callbacks, reports
  payments.py            Recording results and payer details; MTN token cache
  dashboard_urls.py      Dashboard routes
  forms.py               Application and staff login forms
  models.py              EBill (payments), Application, CallBacks
  management/commands/   recheck_pending
  templatetags/          money, status and JSON display filters
  tests.py
templates/dashboard/     Dashboard templates
static/dashboard/        Dashboard CSS and IBS / eBilling logos
docs/STAFF_MANUAL.md     Staff user manual
deploy/                  systemd timer for recheck_pending
```

## Deployment notes

- Set `DEBUG=False`, a real `SECRET_KEY` and the production `ALLOWED_HOSTS`.
- Run `python manage.py collectstatic` and serve `staticfiles/` from the web server.
- `MTN_CALLBACK_URL` must be reachable by MTN over HTTPS.
- Run `python manage.py migrate` after every update. Migration `0003_sync_ebill_with_model` removes the unused `meter_credited` column, which otherwise makes new payments fail to save.
- Some existing databases list a migration `0002_ebill_momo_number` that isn't in this repository. It is harmless, but check that production's schema matches the models before deploying.
- Consider scheduling `recheck_pending` every 15 minutes.
