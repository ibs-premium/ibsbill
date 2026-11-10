# eBilling MoMo Gateway — Staff Manual

This manual is for IBS staff who use the eBilling MoMo Gateway dashboard to take MTN Mobile Money payments, follow them up and report on them.

The dashboard lives at **`/dashboard/`** on the gateway server (for example `https://www.ibsbill.org/dashboard/`).

---

## Contents

1. [Signing in and out](#1-signing-in-and-out)
2. [Finding your way around](#2-finding-your-way-around)
3. [Dashboard](#3-dashboard)
4. [Taking a MoMo payment](#4-taking-a-momo-payment)
5. [Transactions](#5-transactions)
6. [Checking payments with MTN](#6-checking-payments-with-mtn)
7. [Reports](#7-reports)
8. [Callback Logs](#8-callback-logs)
9. [Applications](#9-applications)
10. [What the statuses mean](#10-what-the-statuses-mean)
11. [Troubleshooting](#11-troubleshooting)
12. [For administrators](#12-for-administrators)

---

## 1. Signing in and out

1. Open `/dashboard/` in your browser. You are taken to the sign-in page.
2. Enter your **username** and **password**, then select **Login**. Use the eye icon to show or hide the password while typing.
3. You land on the **Dashboard**.

Only **staff accounts** can sign in. If you see *"Your username and password didn't match, or the account isn't staff"*, check your details, then ask an administrator to confirm your account has staff access (see [For administrators](#12-for-administrators)).

**To sign out**, select the **log-out icon** next to your name at the top of the sidebar.

---

## 2. Finding your way around

| Area | What it does |
|---|---|
| **Sidebar** (left) | Your name and the sign-out button at the top, then the pages: Dashboard, MoMo Payment, Transactions, Reports, Callback Logs, Applications and Django Admin. The page you are on is highlighted in green. |
| **Toolbar** (top) | The panel button hides or shows the sidebar. The breadcrumb (*Home › Transactions › …*) shows where you are; select any part of it to go back. |
| **Page title** | The large heading, with the page's main buttons on the right (for example **Export CSV**). |

On a phone, the sidebar is hidden. Tap the panel button to open it and tap outside it to close it.

---

## 3. Dashboard

The Dashboard gives a quick picture of all payments.

- **Collected**: total of all successful payments, with the **deduction** taken from it (1%) and the **net** amount underneath.
- **Transactions**: number of payment requests, with how many are still pending.
- **Success rate**: successful payments as a share of payments that have finished (successful + failed). Pending payments are not counted.
- **Applications**: how many systems are registered to request payments.
- **Last 14 days**: bars show the number of requests per day; the line shows the amount collected.
- **By application**: amount collected and success count for each registered system.
- **Recent transactions**: the latest eight payments. Select a row to open it.
- **MTN callbacks** (at the top): when MTN last called back, how many callbacks arrived in the last 24 hours, and how many need attention (see [Callback Logs](#8-callback-logs)). A yellow warning appears if payments have been pending for over 30 minutes with no callback at all, which usually means MTN cannot reach the gateway.

Buttons: **New MoMo payment** opens the payment page; **Export CSV** downloads every transaction.

---

## 4. Taking a MoMo payment

Use **MoMo Payment** in the sidebar to send a payment request to a customer's phone.

1. Choose the **Application** the payment belongs to (for example `diamond-ebilling`).
2. Enter the customer's **MoMo number**:
   - 10 digits starting with `0` (for example `024 123 4567`), or
   - 12 digits starting with `233` (for example `233241234567`).
   - Spaces are fine. Numbers starting `020`, `027`, `050` or `057` are not MTN numbers and are refused.
3. Enter the **Amount** in cedis.
4. Enter the **Device ID**: the meter serial number being paid for.
5. **Username** is filled in with your username. Change it to the customer's account or room name if your team records it that way.
6. Select **Send payment request**.

The **Status** panel on the right then shows the progress:

| Step | What you see |
|---|---|
| Request sent to MTN | MTN accepted the request. The customer's registered MoMo name and number are shown, so you can confirm you have the right person. |
| Customer approval | The customer gets a prompt on their phone and enters their MoMo PIN. The page checks with MTN every 10 seconds. |
| Final status | **Payment successful** or **Payment failed**. A failure shows the reason MTN gave, for example *the customer did not approve in time*, *not enough money in the wallet* or *the customer declined or entered the wrong PIN*. |

If there is no answer after **5 minutes**, the page stops checking and shows **Still pending**. The payment is not lost: open it from **View transaction** later, or use [Check status with MTN](#6-checking-payments-with-mtn).

If you see **Request not sent**, MTN refused the request. The message says why; the most common causes are a wrong number or an MTN service problem. Check the number and try again. A request MTN refuses outright is recorded as **Failed**.

**Sent it twice by accident?** If the same application, device, number and amount is sent again within a minute while the first is still pending, the customer is **not** prompted a second time. You are shown the first payment instead.

---

## 5. Transactions

**Transactions** lists every payment request, newest first, **50 per page**. Use the page numbers at the bottom to move around: the current page is highlighted with the page before and after it, **‹** and **›** move one page, and **«** and **»** jump to the first and last page. Your filters are kept as you move between pages.

### Finding a payment

- **Search** matches the reference, payer name, payer number, username, device ID or MTN transaction ID. Part of a value is enough (for example `kofi` or `0241`).
- **Status**: Successful, Pending or Failed.
- **App**: one registered application.
- **From / To**: request dates (both days included).
- Select **Filter** to apply the search and dates. Status and App apply as soon as you change them. **Clear** removes all filters.

### The list

| Column | Meaning |
|---|---|
| Date | When the request was made. |
| Reference | Our reference for the payment (hover to see it in full). Quote this to MTN or the eBilling team. |
| Payer | The payer's registered MoMo name, with their number underneath. *Name unavailable* means MTN did not return a name. |
| App, Username, Device | Which system, account and meter the payment was for. |
| Amount | The amount requested. |
| Status | Successful, Pending or Failed. Hover over **Failed** to see the reason. |
| Credited | A green tick when the meter has been credited. |

**Export CSV** downloads the list with your current filters applied, including the **reason for failure**. The footer shows the total amount of the matching requests.

### A single payment

Select a row to open it. The **Details** panel shows everything recorded, including the payer, the MTN transaction ID, the first response from MTN and, for a failed payment, the **reason for failure**. The **Lifecycle** panel shows each stage: request created, sent to MTN, final status, meter credited and eBilling notified. At the bottom, **Callbacks from MTN** lists every callback MTN sent about this payment and what the gateway made of it.

---

## 6. Checking payments with MTN

Payments normally update themselves when MTN reports the result. When a payment is stuck on **Pending**, you can ask MTN directly.

### One payment

Open the payment and select **Check status with MTN** (top right; only shown while the payment is unfinished). A message tells you the result:

- *MTN reports SUCCESSFUL / FAILED. The transaction has been updated.*
- *MTN reports PENDING. Nothing changed.*: the customer has not answered yet.
- *MTN has no record of this reference*: the request never reached MTN.

### Many payments at once

1. On **Transactions**, filter to the payments you want, for example **Status: Pending**, or a date range.
2. A yellow bar shows how many payments in the list are unfinished. Select **Check N with MTN** and confirm.
3. The payments are checked in small batches. A progress bar and counters show how many came back successful, failed, still pending, not found at MTN, or hit an error.
4. **Stop** halts after the current batch; everything already checked is kept.
5. When it finishes, select **Reload list**.

**Mark ones MTN has no record of as failed**: tick this only when cleaning up old requests. It marks requests that never reached MTN as **Failed** so they stop showing as pending. Leave it unticked for recent payments.

Checking also fills in the payer's number and MoMo name on older payments that don't have them yet, and records the reason for any payment MTN reports as failed.

---

## 7. Reports

**Reports** summarises payments for a period.

1. Pick the period type: **Monthly**, **Quarterly**, **Half-yearly**, **Yearly** or **Selected period**.
2. Pick the year and month, quarter (Q1 = Jan–Mar, Q2 = Apr–Jun, Q3 = Jul–Sep, Q4 = Oct–Dec) or half (H1 = Jan–Jun, H2 = Jul–Dec). For **Selected period**, pick the **From** and **To** dates.
3. Optionally pick one **Application**.
4. Select **Run report**.

The report shows:

- **Collected**, **Requests**, **Success rate** and **Average payment** (per successful payment). Under Collected you see the **deduction (1%)** and the **net** amount.
- A **▲ / ▼ percentage** comparing Collected and Requests with the previous period of the same length (for example Q2 against Q1, or March against February). A dash means the previous period had nothing to compare.
- A **trend chart**: by day for periods up to about two months, by month for longer ones.
- **Breakdown** table with a total row, showing for each day or month the requests, successful, failed and pending counts, **Rate (%)**, **Collected**, the **Deduction (1%)** and the **Net**. Select a day or month to see its transactions.
- **By application** table with the same columns.

The 1% deduction is worked out on **successful payments only** and rounded to 2 decimal places for each day or month, so the Total row is the sum of the rows above it. Failed and pending payments carry no deduction because no money was collected.

**Export CSV** downloads the breakdown, including the deduction and net columns.

**Export PDF** downloads a finished report to share or file: the IBS logo and watermark, the summary figures, a chart, the breakdown by day or month and the by-application table, stamped with the date and your username.

---

## 8. Callback Logs

When a customer approves, declines or ignores a payment prompt, MTN sends the gateway a **callback**. Every callback is recorded here exactly as received. Use it when investigating a disputed or stuck payment.

At the top of the page:

- **Last callback received** and **callbacks in the last 24 hours** show whether MTN is reaching the gateway. If the last callback was a long time ago while payments are being made, something is wrong. Tell an administrator.
- **Need attention (last 7 days)** counts callbacks that deserve a look. Select the number to see them.
- A yellow warning appears when payments from the last 3 days have been pending for over 30 minutes with **no callback at all**. MTN calls back for every request it accepts, so this points to the callback address not being reachable. In the meantime, use [Check status with MTN](#6-checking-payments-with-mtn) to settle them.

### The list

Each row shows when the callback arrived, **which payment it belongs to** (select the reference to open the payment, with its amount and status), what the callback **said**, the **outcome**, and the IP address it came from. It is shown 50 to a page, newest first.

- Search by text in the message (for example a number), by payment reference or by IP address.
- Filter by **outcome**, or choose **Needs attention** to see only the callbacks that deserve a look.

### Outcomes

The gateway does not simply accept what a callback says. It uses the callback to find the payment, then **asks MTN** for the real result before recording it. The outcome says what happened:

| Outcome | Meaning | What to do |
|---|---|---|
| **Recorded** | MTN confirmed the result and it was saved on the payment. | Nothing. |
| **Already recorded** | The payment already had its final result (MTN sometimes sends a callback twice). | Nothing. |
| **Still pending** | MTN says the customer has not answered yet. | Wait. |
| **Not confirmed by MTN** | The callback claimed a final result but MTN did not confirm it. Usually MTN's own status had not caught up yet. | Check the payment with MTN in a few minutes. If it keeps happening for one payment, treat it as suspicious. |
| **Disagrees with MTN** | The callback claimed one final result and MTN reports a different one. MTN's answer was recorded. | **Look into this.** Tell an administrator. |
| **Unknown reference** | The callback names a payment the gateway has no record of. MTN only calls about payments we sent. | Tell an administrator. |
| **Could not check with MTN** | The gateway could not reach MTN to confirm, so nothing was recorded. | Check the payment with MTN. |
| **Not valid JSON** | The message could not be read. | Tell an administrator. |

Callbacks received before outcomes were recorded show a dash instead of an outcome.

### A single callback

Select a row to see the full message (the payment reference, the status MTN reported, the payer and the transaction ID), its headers and where it came from. The page shows what the callback said against what MTN said when the gateway checked, links to the payment, and has a **Check status with MTN** button while the payment is unfinished.

A callback in this list is a record of what was received, not proof that a payment succeeded; always check the payment itself on **Transactions**.

---

## 9. Applications

Only **registered applications** can request payments through the gateway. Each has a name, a web address, the **IP addresses** it calls from and a key. When an application asks for a payment, the gateway checks that the **key** matches and that the request comes from one of the registered **IP addresses**; otherwise it is refused.

An application that runs on several servers needs all of their addresses. Enter them separated by commas, and use a range for a whole group, for example `10.0.4.170, 10.0.4.218, 10.0.5.0/24` (`/24` covers every address from `10.0.5.0` to `10.0.5.255`).

- **Register an application**: fill in the form on the right and select **Register**. Leave **Key** blank to generate a long random one. Copy the key with the clipboard icon and give it to the system's developers.
- **Edit**: change the details and select **Save changes**. If you change the key or the IP address, the application stops working until its developers update their side to match.
- **Remove app**: on the edit page. The application can no longer request payments; its past transactions are kept.

The table also shows how many requests each application has made and how much it has collected.

---

## 10. What the statuses mean

| Status | Meaning | What to do |
|---|---|---|
| **Pending** | Sent to MTN and waiting for the customer, or the result hasn't reached us yet. | Wait a few minutes, then [check with MTN](#6-checking-payments-with-mtn). |
| **Successful** | The customer approved and the money was collected. | Nothing on the gateway. The meter is credited on the eBilling side. |
| **Failed** | The payment did not go through. The reason is shown on the payment (see below). | Ask the customer to try again with a new request. |
| **Credited** (tick) | The meter has been marked as credited for this payment. | If a successful payment isn't credited, contact the eBilling team with the reference. |

### Reasons for a failed payment

MTN gives a reason with every failure. It is shown when you hover over **Failed** in the list, on the payment's page and on the payment screen.

| Reason shown | What it means | What to do |
|---|---|---|
| Customer did not approve in time (PIN not entered before the prompt expired) | The prompt went to the phone but the customer did not enter their PIN in time. | Ask them to try again and watch for the prompt. |
| Customer declined the prompt, or entered the wrong PIN | They pressed cancel, or the PIN was wrong. | Ask them to try again, with the correct PIN. |
| Not enough money in the wallet | The MoMo balance is lower than the amount. | Ask them to top up, or pay a smaller amount. |
| Not a registered MTN MoMo account | The number has no MoMo wallet. | Check the number. |
| Customer's MoMo limit reached | A daily or monthly limit on their account. | Try again later or use another number. |
| MTN had a problem / MTN MoMo was unavailable | A fault on MTN's side. | Try again shortly. |
| A configuration problem (collection account, currency, callback address) | Something is wrong in the gateway's MTN settings. | Tell an administrator. |

MTN does not always say exactly what went wrong. A customer who never enters their PIN usually shows as *did not approve in time*, and a wrong PIN usually shows as *declined or wrong PIN*. Older failed payments may not have a reason recorded.

---

## 11. Troubleshooting

| Message or problem | Cause and fix |
|---|---|
| *Could not get an access token from MTN* | The gateway couldn't sign in to MTN. Usually the MTN credentials are wrong or MTN is down. Tell an administrator. |
| *Enter a valid MTN MoMo number* | The number isn't 10 digits starting with 0, or 12 digits starting with 233, or isn't an MTN number. |
| *Choose a registered application* | The application you picked isn't registered. Add it on **Applications**. |
| An application is told *Invalid application credentials* | Its name, key or IP address doesn't match what is registered. Check the **Applications** page, or ask the application's developers to check what they send. |
| *Too many requests* | An application, or one IP address, sent far more requests than normal in a minute. Wait a minute and try again. |
| Payment stuck on **Pending** | The customer hasn't answered, or MTN's result didn't arrive. Use **Check status with MTN**. |
| Payer shows **Name unavailable** | MTN didn't return a name for that number. The payment is not affected. |
| Payer shows **—** | Older payment with no number recorded. Checking it with MTN fills it in. |
| A failed payment shows no reason | MTN didn't give one, or it is an older payment. An administrator can fill in the missing ones (see below). |
| Signed out unexpectedly | Your session expired. Sign in again. |

---

## 12. For administrators

### Giving someone access

Staff accounts are managed in **Django Admin** (sidebar link, superusers only):

1. **Authentication and Authorization › Users › Add user**: set a username and password, then save.
2. On the next screen fill in their name and email, tick **Staff status**, then save.

To remove access, untick **Active** (keeps their history) rather than deleting the user.

The same Django Admin also lists **Applications**, **E bills** (the payments, searchable and filterable, with the status shown in colour) and **Call backs** (the callback logs, view only). Payments are best looked at on the dashboard; use the admin for corrections only.

### Clearing pending payments automatically

The same check as the bulk button can be run on the server:

```
python manage.py recheck_pending --dry-run          # preview, saves nothing
python manage.py recheck_pending                    # record results
python manage.py recheck_pending --fail-not-found   # also fail requests MTN has no record of
```

It skips requests less than 10 minutes old (change with `--older-than MINUTES`). It is safe to schedule: on a Linux server, copy the two files in the `deploy/` folder to `/etc/systemd/system/` and run `systemctl enable --now ibsbill-recheck.timer` to run it every 15 minutes (cron or Windows Task Scheduler work too).

To add the failure reason to older failed payments that don't have one:

```
python manage.py recheck_pending --backfill-reasons --dry-run   # preview
python manage.py recheck_pending --backfill-reasons
```

### Settings an administrator may change

These go in the server's `.env` file (see [README.md](../README.md)):

| Setting | Effect |
|---|---|
| `COLLECTION_FEE_PERCENT` | The deduction shown in reports (default 1). |
| `ENFORCE_APP_IP` | Whether a payment request must come from the application's registered IP address (default on). |
| `TRUSTED_PROXY_COUNT` | How many web servers sit in front of the gateway (default 1), so the real IP address is read correctly. |

See [README.md](../README.md) for installation, configuration and the API used by eBilling.
