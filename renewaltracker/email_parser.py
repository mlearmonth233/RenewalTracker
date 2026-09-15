"""Heuristic extraction of renewal details from e-mail confirmations.

The parser accepts either a raw RFC-822 message (an ``.eml`` file) or plain
pasted text. It returns a *proposal* – a dictionary of the fields a
``TrackedItem`` needs, each accompanied by enough context for the user to
review and correct it before anything is saved.
"""
from __future__ import annotations

import email
import email.utils
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from email import policy
from email.message import EmailMessage
from html import unescape
from html.parser import HTMLParser

from dateutil import parser as dateparser

from .models import CATEGORIES

# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "passport": [(r"\bpassport\b", 10), (r"\btravel document\b", 4), (r"\bHM Passport Office\b", 10)],
    "insurance": [
        (r"\binsurance\b", 6),
        (r"\binsurer\b", 6),
        (r"\bpolicy\b", 4),
        (r"\bpremium\b", 2),
        (r"\bcover(?:age)?\b", 2),
        (r"\bexcess\b", 2),
        (r"\bunderwrit", 3),
    ],
    "subscription": [
        (r"\bsubscription\b", 6),
        (r"\bsubscribe[sd]?\b", 3),
        (r"\bmembership\b", 5),
        (r"\bplan\b", 2),
        (r"\bstreaming\b", 3),
        (r"\bfree trial\b", 4),
        (r"\bauto[- ]?renew", 3),
    ],
    "bill": [
        (r"\bbill\b", 6),
        (r"\binvoice\b", 6),
        (r"\bstatement\b", 4),
        (r"\butilit(?:y|ies)\b", 4),
        (r"\b(?:gas|electricity|energy|water|broadband|council tax|mortgage|rent)\b", 3),
        (r"\bamount due\b", 3),
        (r"\bdirect debit\b", 2),
    ],
}

# Words that suggest a nearby date is the renewal/expiry/due date.
POSITIVE_DATE_CONTEXT = [
    (r"renew(?:s|al|ed|ing)?", 6),
    (r"expir(?:e|es|y|ation|ing)", 6),
    (r"valid (?:until|to|through|thru)", 6),
    (r"due", 5),
    (r"next (?:payment|billing|bill|charge|invoice|renewal)", 6),
    (r"(?:will|to) be (?:charged|billed|taken|debited)", 5),
    (r"until", 3),
    (r"end(?:s|ing)? (?:on|date)", 4),
    (r"cover (?:ends|runs|expires)", 6),
    (r"payment date", 5),
    (r"on or before", 4),
]

# Words that suggest a nearby date is NOT the renewal date.
NEGATIVE_DATE_CONTEXT = [
    (r"issued?", 6),
    (r"date of issue", 8),
    (r"date of birth|born|dob", 10),
    (r"order(?:ed)? (?:date|on|placed)", 6),
    (r"purchase[ds]?(?: on| date)?", 5),
    (r"start(?:s|ed|ing)? (?:on|date|from)", 5),
    (r"effective (?:from|date)", 4),
    (r"sent|received|thank you for your payment on", 3),
    (r"payment (?:received|confirmed|of .{0,20} on)", 5),
    (r"joined|member since|since", 4),
]

AMOUNT_CONTEXT = [
    (r"total", 5),
    (r"amount", 4),
    (r"price", 3),
    (r"premium", 4),
    (r"charged?", 3),
    (r"payment", 2),
    (r"cost", 3),
    (r"fee", 2),
    (r"due", 3),
    (r"renewal", 3),
]

RECURRENCE_PATTERNS = [
    ("weekly", r"\b(?:weekly|per week|/\s?week|every week|a week)\b"),
    ("quarterly", r"\b(?:quarterly|every (?:3|three) months|per quarter)\b"),
    ("yearly", r"\b(?:annual(?:ly)?|yearly|per (?:year|annum)|/\s?(?:year|yr)|every (?:12 months|year)|12[- ]month|a year)\b"),
    ("monthly", r"\b(?:monthly|per month|/\s?(?:month|mo)|every month|each month|a month)\b"),
]

CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}
CURRENCY_CODES = {"GBP", "USD", "EUR", "AUD", "CAD", "NZD", "CHF", "JPY", "INR", "SGD"}

MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

DATE_PATTERNS = [
    # 2026-03-14 or 2026/03/14
    re.compile(r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b"),
    # 14/03/2026, 14.03.26, 3-14-2026
    re.compile(r"\b(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})\b"),
    # 14 March 2026, 14th Mar 2026, 14 March, 2026
    re.compile(r"\b(\d{1,2}(?:st|nd|rd|th)?\s+" + MONTHS + r"\.?,?\s+\d{4})\b", re.IGNORECASE),
    # March 14, 2026 / Mar 14th 2026
    re.compile(r"\b(" + MONTHS + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\b", re.IGNORECASE),
    # 14 March (no year) – year inferred as next occurrence
    re.compile(r"\b(\d{1,2}(?:st|nd|rd|th)?\s+" + MONTHS + r")\b(?!\s*\d{4})", re.IGNORECASE),
    re.compile(r"\b(" + MONTHS + r"\s+\d{1,2}(?:st|nd|rd|th)?)\b(?!,?\s*\d{4})", re.IGNORECASE),
]

AMOUNT_PATTERNS = [
    # £12.99, $ 1,299.00, €45
    re.compile(r"(£|\$|€)\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"),
    # GBP 12.99 / 12.99 GBP
    re.compile(r"\b(" + "|".join(CURRENCY_CODES) + r")\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\b"),
    re.compile(r"\b(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s?(" + "|".join(CURRENCY_CODES) + r")\b"),
]

REFERENCE_PATTERN = re.compile(
    r"(?:policy|account|reference|ref|membership|customer|passport|order|contract|agreement)"
    r"\s*(?:no\.?|number|num|#|id)?\s*[:#\-]?\s*([A-Z0-9][A-Z0-9\-/]{3,24}(?: \d{2,6}){0,4})",
    re.IGNORECASE,
)

NOREPLY = re.compile(r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|noreply|notifications?|mailer|alerts?|info|support|hello|team|billing|service)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    """Strip HTML to readable text, keeping line breaks around block tags."""

    BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "td", "th"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self._skip += 1
        elif tag in self.BLOCK_TAGS:
            self._chunks.append("\n" if tag != "td" and tag != "th" else " ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"} and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK_TAGS:
            self._chunks.append("\n" if tag != "td" and tag != "th" else " ")

    def handle_data(self, data):
        if not self._skip:
            self._chunks.append(data)

    def text(self) -> str:
        raw = unescape("".join(self._chunks))
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n", raw)
        return raw.strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def _looks_like_rfc822(text: str) -> bool:
    head = text[:4000]
    return bool(re.search(r"^(From|Subject|To|Date|Received|Return-Path|MIME-Version):", head, re.MULTILINE | re.IGNORECASE))


def _decode_payload(part: EmailMessage) -> str:
    """Decode a MIME part's text, coping with missing or wrong charsets.

    Many saved e-mails (and pasted text with a ``Subject:`` line) carry no
    ``Content-Type`` charset. The ``email`` package then decodes as US-ASCII
    and turns every ``£`` or ``€`` into U+FFFD, so we fall back to the raw
    bytes and try UTF-8 first.
    """
    content = None
    try:
        content = part.get_content()
    except Exception:  # malformed transfer encoding, unknown charset, ...
        content = None
    if isinstance(content, str) and "�" not in content:
        return content
    raw = part.get_payload(decode=True)
    if not isinstance(raw, bytes):
        return content or str(part.get_payload() or "")
    for charset in ("utf-8", part.get_content_charset() or "", "cp1252", "latin-1"):
        if not charset:
            continue
        try:
            decoded = raw.decode(charset)
        except (UnicodeDecodeError, LookupError):
            continue
        if "�" not in decoded:
            return decoded
    return raw.decode("utf-8", errors="replace")


def _message_body(msg: EmailMessage) -> str:
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    content = _decode_payload(body)
    if body.get_content_type() == "text/html":
        return html_to_text(content)
    return content


def _context_score(text: str, start: int, end: int, table, window: int = 70) -> int:
    before = text[max(0, start - window):start].lower()
    after = text[end:end + 25].lower()
    score = 0
    for pattern, weight in table:
        if re.search(pattern, before):
            score += weight
        # Only a short look-ahead: "14 March 2026 (expiry)" style.
        elif re.search(pattern, after):
            score += weight // 2
    return score


def _resolve_two_digit_year(text: str) -> str:
    """dateutil handles 2-digit years but we normalise 26 -> 2026 for safety."""
    m = re.match(r"^(\d{1,2})([/.\-])(\d{1,2})\2(\d{2})$", text)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(2)}20{m.group(4)}"
    return text


def _parse_date_token(token: str, dayfirst: bool, today: date) -> date | None:
    token = _resolve_two_digit_year(token.strip())
    numeric = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$", token)
    if numeric:
        a, b = int(numeric.group(1)), int(numeric.group(2))
        # Disambiguate when one component cannot be a month.
        if a > 12 and b <= 12:
            dayfirst = True
        elif b > 12 and a <= 12:
            dayfirst = False
    try:
        parsed = dateparser.parse(token, dayfirst=dayfirst, default=_default_dt(today))
    except (ValueError, OverflowError):
        return None
    result = parsed.date()
    has_year = bool(re.search(r"\d{4}", token))
    if not has_year and result < today:
        # "14 March" with no year: assume the next occurrence.
        result = result.replace(year=result.year + 1)
    if result.year < 2000 or result.year > today.year + 30:
        return None
    return result


def _default_dt(today: date) -> datetime:
    return datetime(today.year, today.month, today.day)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class ParsedEmail:
    subject: str | None = None
    sender: str | None = None
    provider: str | None = None
    category: str = "other"
    category_scores: dict = field(default_factory=dict)
    name: str | None = None
    amount: float | None = None
    currency: str | None = None
    renewal_date: date | None = None
    date_candidates: list = field(default_factory=list)
    recurrence: str = "none"
    reference: str | None = None
    auto_renews: bool = False
    reminder_days: str = "7,1"
    body_excerpt: str = ""
    confidence: float = 0.0
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "sender": self.sender,
            "provider": self.provider,
            "category": self.category,
            "category_scores": self.category_scores,
            "name": self.name,
            "amount": self.amount,
            "currency": self.currency,
            "renewal_date": self.renewal_date.isoformat() if self.renewal_date else None,
            "date_candidates": [
                {"date": d.isoformat(), "score": s, "text": t} for d, s, t in self.date_candidates
            ],
            "recurrence": self.recurrence,
            "reference": self.reference,
            "auto_renews": self.auto_renews,
            "reminder_days": self.reminder_days,
            "body_excerpt": self.body_excerpt,
            "confidence": self.confidence,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_email(raw: bytes | str, *, dayfirst: bool = True, today: date | None = None) -> ParsedEmail:
    """Parse an e-mail (``.eml`` bytes or pasted text) into a renewal proposal."""
    today = today or date.today()
    result = ParsedEmail()

    if isinstance(raw, bytes):
        text_guess = raw.decode("utf-8", errors="replace")
    else:
        text_guess = raw

    if _looks_like_rfc822(text_guess):
        # Always parse from bytes so undeclared charsets can be recovered
        # from the raw payload (see _decode_payload).
        raw_bytes = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        msg = email.message_from_bytes(raw_bytes, policy=policy.default)
        result.subject = (msg.get("subject") or "").strip() or None
        result.sender = (msg.get("from") or "").strip() or None
        body = _message_body(msg)
    else:
        body = text_guess
        if "<html" in body.lower() or "<body" in body.lower() or re.search(r"<\w+[^>]*>", body[:2000]):
            body = html_to_text(body)
        # A pasted e-mail often starts with "Subject: ..." on its own line.
        m = re.search(r"^subject:\s*(.+)$", body, re.IGNORECASE | re.MULTILINE)
        if m:
            result.subject = m.group(1).strip()
        m = re.search(r"^from:\s*(.+)$", body, re.IGNORECASE | re.MULTILINE)
        if m:
            result.sender = m.group(1).strip()

    body = body.replace("\xa0", " ")
    result.body_excerpt = body[:1500]
    haystack = f"{result.subject or ''}\n{body}"

    result.provider = _extract_provider(result.sender, haystack)
    result.category, result.category_scores = _classify(haystack)
    result.amount, result.currency = _extract_amount(haystack)
    result.renewal_date, result.date_candidates = _extract_renewal_date(haystack, dayfirst, today)
    result.recurrence = _extract_recurrence(haystack, result.category)
    result.reference = _extract_reference(haystack)
    result.auto_renews = bool(
        re.search(
            r"auto[- ]?renew|automatically renew|renews? automatically|will (?:automatically )?be renewed"
            r"|continue(?:s)? automatically|unless you (?:cancel|tell us otherwise)",
            haystack,
            re.IGNORECASE,
        )
    )
    result.reminder_days = CATEGORIES[result.category]["default_reminder_days"]
    result.name = _build_name(result)

    filled = sum(1 for v in (result.provider, result.amount, result.renewal_date, result.reference) if v)
    result.confidence = round(min(1.0, 0.2 + 0.2 * filled + (0.1 if result.category != "other" else 0)), 2)

    if result.renewal_date is None:
        result.warnings.append("No renewal or expiry date could be found. Please enter it manually.")
    elif result.renewal_date < today:
        result.warnings.append("The detected date is in the past. Check whether this is the correct renewal date.")
    if result.amount is None and result.category != "passport":
        result.warnings.append("No amount was detected.")
    if result.category == "other":
        result.warnings.append("Could not determine the type of item. Please choose a category.")
    return result


# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------


def _extract_provider(sender: str | None, haystack: str) -> str | None:
    if sender:
        name, addr = email.utils.parseaddr(sender)
        name = name.strip().strip('"')
        if name and not NOREPLY.fullmatch(name.replace(" ", "")):
            # "Netflix <info@...>" -> "Netflix"; strip trailing "Team", "Billing" etc.
            cleaned = re.sub(
                r"\b(?:team|billing|support|customer(?: (?:service|care|team|services))?|notifications?|no[- ]?reply|do not reply)\b",
                "",
                name,
                flags=re.IGNORECASE,
            ).strip(" -|,")
            if cleaned:
                return cleaned[:200]
        if addr and "@" in addr:
            domain = addr.split("@", 1)[1].lower()
            parts = [p for p in domain.split(".") if p not in {"com", "co", "uk", "org", "net", "io", "gov", "mail", "email", "info", "notifications", "e", "em", "news"}]
            if parts:
                return parts[-1].capitalize()
    m = re.search(r"thank you for (?:choosing|being (?:a|with)|renewing with|your .{0,20}with)\s+([A-Z][\w&'. ]{1,40}?)(?:[.,!\n]|$)", haystack)
    if m:
        return m.group(1).strip()
    return None


def _classify(haystack: str) -> tuple[str, dict]:
    scores: dict[str, int] = {}
    for category, table in CATEGORY_KEYWORDS.items():
        total = 0
        for pattern, weight in table:
            hits = len(re.findall(pattern, haystack, re.IGNORECASE))
            if hits:
                total += weight * min(hits, 3)
        scores[category] = total
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "other", scores
    return best, scores


def _extract_amount(haystack: str) -> tuple[float | None, str | None]:
    best: tuple[int, float, str] | None = None
    for pattern in AMOUNT_PATTERNS:
        for m in pattern.finditer(haystack):
            g1, g2 = m.group(1), m.group(2)
            if g1 in CURRENCY_SYMBOLS:
                currency, number = CURRENCY_SYMBOLS[g1], g2
            elif g1.upper() in CURRENCY_CODES:
                currency, number = g1.upper(), g2
            else:
                currency, number = g2.upper(), g1
            try:
                value = float(number.replace(",", ""))
            except ValueError:
                continue
            if value <= 0 or value > 1_000_000:
                continue
            score = _context_score(haystack, m.start(), m.end(), AMOUNT_CONTEXT)
            # Prefer amounts with decimals slightly – "£12.99" over "£12".
            if "." in number:
                score += 1
            if best is None or score > best[0]:
                best = (score, value, currency)
    if best is None:
        return None, None
    return best[1], best[2]


def _extract_renewal_date(haystack: str, dayfirst: bool, today: date) -> tuple[date | None, list]:
    candidates: dict[date, tuple[int, str]] = {}
    seen_spans: list[tuple[int, int]] = []
    for pattern in DATE_PATTERNS:
        for m in pattern.finditer(haystack):
            span = (m.start(1), m.end(1))
            if any(s <= span[0] and span[1] <= e for s, e in seen_spans):
                continue
            parsed = _parse_date_token(m.group(1), dayfirst, today)
            if parsed is None:
                continue
            seen_spans.append(span)
            score = _context_score(haystack, span[0], span[1], POSITIVE_DATE_CONTEXT)
            score -= _context_score(haystack, span[0], span[1], NEGATIVE_DATE_CONTEXT)
            if parsed >= today:
                score += 3
            else:
                score -= 4
            prev = candidates.get(parsed)
            if prev is None or score > prev[0]:
                candidates[parsed] = (score, m.group(1))
    ranked = sorted(((d, s, t) for d, (s, t) in candidates.items()), key=lambda x: (-x[1], x[0]))
    if not ranked:
        return None, []
    best_date, best_score, _ = ranked[0]
    # If nothing has positive context, fall back to the latest future date.
    if best_score <= 0:
        future = [c for c in ranked if c[0] >= today]
        if future:
            best_date = max(future, key=lambda c: c[0])[0]
    return best_date, ranked[:6]


def _extract_recurrence(haystack: str, category: str) -> str:
    if category == "passport":
        return "none"
    for recurrence, pattern in RECURRENCE_PATTERNS:
        if re.search(pattern, haystack, re.IGNORECASE):
            return recurrence
    return CATEGORIES[category]["default_recurrence"] if category != "other" else "none"


def _extract_reference(haystack: str) -> str | None:
    for m in REFERENCE_PATTERN.finditer(haystack):
        ref = m.group(1).strip().rstrip(".,;:")
        # Must contain at least one digit and not look like a date or amount.
        if re.search(r"\d", ref) and not re.fullmatch(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}", ref) and not re.fullmatch(r"\d{4}", ref):
            return ref
    return None


def _build_name(result: ParsedEmail) -> str:
    label = CATEGORIES[result.category]["label"]
    if result.provider:
        if result.category == "other":
            return result.provider
        return f"{result.provider} {label.lower() if result.category != 'passport' else 'passport'}"
    if result.subject:
        subject = re.sub(r"^(re|fwd?|fw):\s*", "", result.subject, flags=re.IGNORECASE)
        return subject[:200]
    return label
