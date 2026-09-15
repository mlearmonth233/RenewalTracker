from datetime import date

from renewaltracker.email_parser import html_to_text, parse_email

TODAY = date(2026, 9, 15)

INSURANCE_EML = b"""From: Aviva Customer Team <no-reply@aviva.co.uk>
To: michael@example.com
Subject: Your car insurance renewal
Date: Mon, 1 Sep 2026 09:00:00 +0100
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"

Hi Michael,

Your car insurance policy is due for renewal.

Policy number: AV-99887766
Cover ends: 14 October 2026
Your annual renewal premium: \xc2\xa3412.50

Your policy started on 14 October 2025. Unless you tell us otherwise, your
policy will automatically renew.

Thanks,
Aviva
"""

SUBSCRIPTION_HTML_EML = b"""From: Netflix <info@mailer.netflix.com>
Subject: Your next billing date
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"

<html><body>
<p>Hi there,</p>
<p>Your <b>Premium plan</b> subscription will renew on <b>September 28, 2026</b>.</p>
<table><tr><td>Monthly price</td><td>$15.49</td></tr></table>
<p>Order date: August 28, 2026</p>
</body></html>
"""

PASSPORT_TEXT = """Subject: Your passport is ready
From: HM Passport Office <noreply@hmpo.gov.uk>

Dear applicant,

Your new passport has been issued. Passport number: 533112244
Date of issue: 05/09/2026
Date of expiry: 05/09/2036

Please check your details carefully.
"""

BILL_TEXT = """Subject: Your energy bill is ready

Account number 8800 1234 5678
Amount due: £128.40
Payment due date: 30/09/2026
Bill date: 10/09/2026
This bill covers 10/08/2026 to 09/09/2026.
"""


def test_parses_insurance_eml():
    result = parse_email(INSURANCE_EML, today=TODAY)
    assert result.subject == "Your car insurance renewal"
    assert result.category == "insurance"
    assert result.provider == "Aviva"
    assert result.amount == 412.50
    assert result.currency == "GBP"
    assert result.renewal_date == date(2026, 10, 14)
    assert result.recurrence == "yearly"
    assert result.reference == "AV-99887766"
    assert result.auto_renews is True
    assert result.reminder_days == "30,7"
    assert result.confidence >= 0.8


def test_parses_html_subscription_and_prefers_renewal_over_order_date():
    result = parse_email(SUBSCRIPTION_HTML_EML, today=TODAY)
    assert result.category == "subscription"
    assert result.provider == "Netflix"
    assert result.amount == 15.49
    assert result.currency == "USD"
    assert result.renewal_date == date(2026, 9, 28)
    assert result.recurrence == "monthly"
    # Both dates are surfaced as candidates for the user to pick from.
    candidate_dates = {c[0] for c in result.date_candidates}
    assert date(2026, 8, 28) in candidate_dates


def test_parses_pasted_passport_text_picks_expiry_not_issue_date():
    result = parse_email(PASSPORT_TEXT, today=TODAY)
    assert result.category == "passport"
    assert result.renewal_date == date(2036, 9, 5)
    assert result.recurrence == "none"
    assert result.reference == "533112244"
    assert result.reminder_days == "270,180,90"
    assert result.amount is None
    assert "passport" in result.name.lower()


def test_parses_bill_with_day_first_dates():
    result = parse_email(BILL_TEXT, today=TODAY, dayfirst=True)
    assert result.category == "bill"
    assert result.amount == 128.40
    assert result.renewal_date == date(2026, 9, 30)
    assert result.reference == "8800 1234 5678"


def test_month_first_config_is_respected_for_ambiguous_dates():
    text = "Subject: Invoice\nAmount due $40.00. Payment due 03/04/2027."
    assert parse_email(text, today=TODAY, dayfirst=True).renewal_date == date(2027, 4, 3)
    assert parse_email(text, today=TODAY, dayfirst=False).renewal_date == date(2027, 3, 4)


def test_unambiguous_numeric_date_overrides_dayfirst():
    text = "Your plan renews on 04/25/2027 for $9.99 per month."
    result = parse_email(text, today=TODAY, dayfirst=True)
    assert result.renewal_date == date(2027, 4, 25)


def test_date_without_year_assumes_next_occurrence():
    text = "Subject: Membership\nYour gym membership renews on 3 January. Monthly fee £29."
    result = parse_email(text, today=TODAY)
    assert result.renewal_date == date(2027, 1, 3)
    assert result.recurrence == "monthly"
    assert result.category == "subscription"


def test_no_date_produces_warning_and_low_confidence():
    result = parse_email("Thanks for your payment of £5.", today=TODAY)
    assert result.renewal_date is None
    assert any("No renewal" in w for w in result.warnings)
    assert result.category == "other"


def test_html_to_text_strips_tags_and_scripts():
    html = "<html><head><style>p{}</style></head><body><p>Hello&nbsp;<b>world</b></p><script>x()</script><div>Bye</div></body></html>"
    text = html_to_text(html)
    assert "Hello" in text and "world" in text and "Bye" in text
    assert "x()" not in text and "p{}" not in text
