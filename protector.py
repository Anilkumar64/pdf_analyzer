"""
Layer 2: Banking-Focused Content Protection

Goal (for banking application):
  - Translate ONLY labels + Terms & Conditions / disclaimers / instructions
  - NEVER touch dynamic values (loan numbers, amounts, names, dates,
    reference numbers, account numbers, emails, phones, IFSC, etc.)

Decision order (strict):
  1. Strong value patterns          → protect
  2. Clear banking labels           → translate
  3. Long static paragraphs (T&C)   → translate
  4. When in doubt                  → protect   (safer default)

Designed for the common banking document patterns:
  Payment advice, Loan statement, Account statement, Certificates,
  Sanction letters, Transaction receipts, etc.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from extractor import TextSpan, DocumentData

_HERE = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------------ types ----

@dataclass
class ProtectedPart:
    text: str
    is_protected: bool
    placeholder: str | None = None


@dataclass
class ProtectedSpan:
    original_span: TextSpan
    parts: List[ProtectedPart]
    final_text_for_translation: str
    units: List[str] = field(default_factory=list)
    template: str = ""
    uses_placeholders: bool = False


@dataclass
class ProtectionResult:
    document: DocumentData
    protected_spans: List[ProtectedSpan]
    value_map: Dict[str, str]
    stats: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------- keep list --

# Tokens that must NEVER be translated
DEFAULT_KEEP_TOKENS: Set[str] = {
    "GSTIN", "GST", "CGST", "SGST", "UGST", "IGST", "HSN", "SAC", "PAN", "TAN",
    "CIN", "IRN", "UPI", "IFSC", "IBAN", "SWIFT", "VAT", "TIN", "SKU", "QR",
    "OTP", "URL", "PDF", "B2B", "B2C", "INR", "USD", "EUR", "GBP", "AWB",
    "NEFT", "RTGS", "IMPS", "GSTR", "FSSAI", "COD", "EMI", "NPA", "KYC",
    "AADHAAR", "AADHAR", "UID", "CKYC", "eKYC",
}

LABEL_ABBREVS: Set[str] = {
    "no", "no.", "sl", "sl.", "sl.no", "sl.no.", "s.no", "s.no.", "s no",
    "sr", "sr.", "sr.no", "sr.no.", "qty", "amt", "rate", "hsn", "sac",
    "po", "so", "ref", "ref.", "id", "id.", "pin", "pin.", "zip",
}


def _load_keep_file() -> Tuple[Set[str], Set[str]]:
    tokens, phrases = set(), set()
    path = os.path.join(_HERE, "keep_terms.txt")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                t = line.strip()
                if not t or t.startswith("#"):
                    continue
                if " " in t:
                    phrases.add(t.lower())
                else:
                    tokens.add(t)
    return tokens, phrases


_FILE_TOKENS, KEEP_PHRASES = _load_keep_file()
KEEP_TOKENS = DEFAULT_KEEP_TOKENS | _FILE_TOKENS


# ----------------------------------------------------------------- patterns --

_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
          r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
          r"Dec(?:ember)?)")
_UNITS = (r"(?:cms?|mm|km|kg|mg|ml|gb|mb|tb|kb|mhz|ghz|hz|kw|mah|inch(?:es)?|"
          r"pcs|nos|lbs?|oz|hrs?|mins?|secs?|yrs?|ft|[mglwv])")

STRONG_VALUE_PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),            # email
    re.compile(r"(?:https?://|www\.)[^\s]*[^\s.,;:)\]]"),                         # url
    re.compile(r"\b\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}\b"),                         # date numeric
    re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\.?,?\s+\d{{4}}\b", re.I),
    re.compile(rf"\b{_MONTH}\.?\s+\d{{4}}\b", re.I),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp][Mm])?\b"),                # time
    re.compile(r"(?:₹|Rs\.?|INR|\$|€|£|¥|USD|EUR|GBP)\s?\d[\d,]*(?:\.\d+)?"),     # money
    re.compile(r"\d[\d,]*(?:\.\d+)?\s?%"),                                        # percent
    re.compile(rf"\b\d+(?:[.,]\d+)*\s?{_UNITS}\b", re.I),
    re.compile(r"\+\d{1,3}[\s-]?\d[\d\s-]{7,}\d"),                                # phone
    re.compile(r"\b\d{3,5}[\s-]\d{3,5}[\s-]\d{3,5}\b"),
    re.compile(r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?"),                                # 7,980.00
    re.compile(r"\b\d+\.\d+\b"),
    re.compile(r"\b\d{6,}\b"),                                                   # long digit strings
    re.compile(r"\b[A-Z]{2,5}\d{6,}\b"),                                         # TC FPL029... style
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                                         # 2024-03-30
]

_ID_LIKE = re.compile(r"\b[A-Za-z]*\d+[A-Za-z0-9\-_/]*\b|\b[A-Za-z]+\d+[A-Za-z0-9\-_/]*\b")
_ORDINAL = re.compile(r"\d+(?:st|nd|rd|th)", re.I)
_TOKEN_EDGE = re.compile(r"^([\(\[\{\"'“‘:;,]*)(.*?)([\)\]\}\"'”’.,;:!?]*)$", re.S)
_EDGE_CHARS = " \t\r\n:;,|/*-–—•·+=#<>()[]{}"
_LATIN = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"\d")
_ALL_CAPS_NAME = re.compile(r"^[A-Z][A-Z\s.'-]{2,}$")


# -------------------------------------------------------- helpers ------------

def _is_keep_token(tok: str) -> bool:
    return tok in KEEP_TOKENS or tok.upper() in KEEP_TOKENS


def _is_label_abbrev(tok: str) -> bool:
    return tok.lower().rstrip(".") in LABEL_ABBREVS


def _looks_like_id(tok: str) -> bool:
    return bool(_ID_LIKE.fullmatch(tok))


def _is_pure_number_or_code(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if re.fullmatch(r"[\d\s,.\-/%₹$€£]+", t):
        return True
    if t.isupper() and len(t) <= 20 and _HAS_DIGIT.search(t):
        return True
    return False


def _looks_like_person_name(text: str) -> bool:
    """Detect person names (Title Case or ALL CAPS). Exclude known labels."""
    t = text.strip()
    if len(t) < 3 or len(t) > 60:
        return False
    if _HAS_DIGIT.search(t):
        return False
    if t.endswith(":"):
        return False

    lower = t.lower()
    # Hard exclude known labels / headers so they are never treated as names
    exclude = {
        "tax invoice", "invoice", "sold by", "bill to", "ship to",
        "billing address", "shipping address", "item code", "serial number",
        "prepared by", "verified by", "authorised signature", "authorized signature",
        "gst summary", "coupon code", "unit price", "cod charge", "shipping charge",
        "grand total", "total amount", "total tax", "store credit", "order type",
        "payment mode", "type of supply", "place of supply", "order currency",
        "goods and services", "amount in words", "thank you", "disclaimer",
        "total", "rate", "amt", "qty", "discount", "value", "taxable",
        "s.no", "sl.no", "sr.no", "hsn", "sac", "cgst", "sgst", "igst",
        "admit card", "receipt", "statement", "loan number", "transaction date",
        "payment for", "amount (rs.)", "transaction reference number",
        "bank reference number", "receipt generated date",
    }
    if lower in exclude:
        return False

    # ALL CAPS names: ANIL KUMAR VENNAPUREDDY, RAHUL RAJESH TRIPATHI
    if _ALL_CAPS_NAME.match(t) and 1 <= len(t.split()) <= 5:
        return True

    # Title Case names (must look like real names, not labels)
    words = [w for w in t.split() if w]
    if (2 <= len(words) <= 5
            and all(w[0].isupper() for w in words if w and w[0].isalpha())
            and not any(w.isupper() and len(w) > 3 for w in words)):
        return True
    return False


def _looks_like_long_reference(text: str) -> bool:
    """Loan numbers, transaction refs, bank refs etc."""
    t = text.strip()
    if len(t) < 6:
        return False
    # Contains both letters and digits and is reasonably long
    if _HAS_DIGIT.search(t) and re.search(r"[A-Za-z]", t) and len(t) >= 8:
        return True
    # Long pure digit strings already caught by patterns
    return False


def _is_banking_label(text: str) -> bool:
    """High-confidence banking field labels."""
    lower = text.lower().rstrip(":").strip()

    banking_labels = {
        # Common field labels
        "loan number", "loan no", "loan a/c", "loan account",
        "transaction reference number", "transaction reference", "txn ref",
        "bank reference number", "bank reference", "bank ref",
        "payment for", "payment towards", "purpose",
        "amount", "amount (rs.)", "amount (rs)", "amount rs", "amount in rs",
        "transaction date", "txn date", "payment date", "value date",
        "receipt generated date", "generated on", "generated date",
        "account number", "a/c number", "a/c no", "account no",
        "customer name", "customer id", "customer id", "cif",
        "branch", "branch name", "branch code", "ifsc", "ifsc code",
        "from account", "to account", "debit account", "credit account",
        "narration", "particulars", "description", "remarks",
        "opening balance", "closing balance", "available balance",
        "transaction amount", "debit", "credit", "balance",
        "emi amount", "emi date", "due date", "overdue", "principal",
        "interest", "charges", "penalty", "late payment",
        "sanction amount", "disbursed amount", "outstanding",
        "maturity date", "maturity amount", "interest rate",
        "tenure", "roi", "rate of interest",
        "pan", "aadhaar", "aadhar", "mobile", "email", "email id",
        "address", "communication address", "permanent address",
        "nominee", "nominee name", "relationship",
        "date", "time", "name", "registration number", "photo id",
        "examination centre code", "paper code", "paper code & name",
        "order no", "invoice no", "invoice number", "order number",
        "gstin", "hsn", "sac", "place of supply", "reverse charge",
        "total", "sub total", "grand total", "net amount",
        "prepared by", "verified by", "authorised signatory",
        "authorised signature", "authorized signature",
        "s.no", "sl.no", "sr.no", "item code", "serial number",
        "unit price", "qty", "quantity", "discount", "taxable value",
        "cgst", "sgst", "igst", "ugst", "tax amount",
        "billing address", "shipping address", "sold by",
        "payment mode", "payment status", "transaction status",
        "status", "remarks", "note", "notes",
    }

    if lower in banking_labels:
        return True
    if any(lower.startswith(lb + " ") or lower.startswith(lb + ":") for lb in banking_labels):
        return True

    # Ends with colon and is short → very likely a label
    if text.strip().endswith(":") and len(text.strip()) <= 45:
        return True

    return False


def _is_terms_or_instruction(text: str) -> bool:
    """Long static paragraphs that should be translated (T&C, disclaimers, instructions)."""
    t = text.strip()
    if len(t) < 40:
        return False
    if _looks_like_person_name(t) or _looks_like_long_reference(t):
        return False
    if _is_pure_number_or_code(t):
        return False

    # Mostly letters, few digits → descriptive text
    letters = sum(1 for c in t if c.isalpha())
    digits = sum(1 for c in t if c.isdigit())
    if letters > 30 and digits < 8:
        return True

    lower = t.lower()
    indicators = (
        "terms and conditions", "disclaimer", "this is a computer generated",
        "does not require a signature", "if you have any queries",
        "please note", "important", "instructions", "candidates will",
        "the amount will reflect", "within 2 working days",
        "customer care", "for any assistance", "thank you",
        "not payable under reverse charge", "system generated",
    )
    if any(ind in lower for ind in indicators):
        return True

    return False


# -------------------------------------------------------- range detection ----

def _protect_piece(piece: str, composite: bool, weak_ok: bool) -> bool:
    if _is_keep_token(piece):
        return True
    if _is_label_abbrev(piece):
        return False
    has_digit = any("0" <= c <= "9" for c in piece)
    if has_digit:
        letters = sum(c.isalpha() for c in piece)
        if letters == 0:
            return True
        if _ORDINAL.fullmatch(piece):
            return False
        if _looks_like_id(piece):
            return True
        if composite:
            return len(piece) >= 2
        return len(piece) >= 4 or (len(piece) >= 3 and any(c.isupper() for c in piece))
    if re.search(r"[a-z][A-Z]", piece):
        return True
    if composite and piece.isupper() and len(piece) >= 2:
        return True
    return False


def _token_ranges(tok: str, weak_ok: bool) -> List[Tuple[int, int]]:
    m = _TOKEN_EDGE.match(tok)
    lead, core = len(m.group(1)), m.group(2)
    if not core:
        return []
    if _is_keep_token(core):
        return [(lead, lead + len(core))]
    pieces = list(re.finditer(r"[^-/_]+", core))
    composite = len(pieces) > 1
    out = [(lead + pm.start(), lead + pm.end())
           for pm in pieces if _protect_piece(pm.group(0), composite, weak_ok)]
    if composite and len(out) == len(pieces):
        return [(lead, lead + len(core))]
    return out


def _merge(ranges: List[Tuple[int, int]], text: str) -> List[Tuple[int, int]]:
    ranges = sorted(ranges)
    out: List[Tuple[int, int]] = []
    for a, b in ranges:
        if out and (a <= out[-1][1] or (a - out[-1][1] == 1 and text[out[-1][1]] in "-/_")):
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _find_value_ranges(text: str) -> List[Tuple[int, int]]:
    weak_ok = len(text.split()) <= 5
    strong: List[Tuple[int, int]] = []
    for pat in STRONG_VALUE_PATTERNS:
        strong.extend((m.start(), m.end()) for m in pat.finditer(text))
    strong = _merge(strong, text)

    ranges = list(strong)
    pos = 0
    for a, b in strong + [(len(text), len(text))]:
        if a > pos:
            for m in re.finditer(r"\S+", text[pos:a]):
                base = pos + m.start()
                ranges.extend((base + x, base + y) for x, y in _token_ranges(m.group(0), weak_ok))
        pos = max(pos, b)
    return _merge(ranges, text)


# ---------------------------------------------------------- classification --

def _is_clear_value(span: TextSpan) -> bool:
    text = span.text.strip()
    if not text:
        return True

    if _is_keep_token(text) or text.upper() in KEEP_TOKENS:
        return True
    if text.lower() in KEEP_PHRASES:
        return True
    if _is_pure_number_or_code(text):
        return True
    if _looks_like_person_name(text):
        return True
    if _looks_like_long_reference(text):
        return True

    # Address-like fragments
    lower = text.lower()
    address_signals = ("road", "street", "village", "nagar", "colony", "layout",
                       "school", "opposite", "near", "india", "state", "district",
                       "telangana", "maharashtra", "karnataka", "chennai", "mumbai",
                       "hyderabad", "bangalore", "pune", "church", "chruch",
                       "layolo", "bhiwandi", "kukasa")
    if any(w in lower for w in address_signals) and not text.endswith(":"):
        return True
    if re.search(r"\b\d{5,6}\b", text):  # pincode
        return True

    # Strong value patterns that dominate the span
    if any(p.search(text) for p in STRONG_VALUE_PATTERNS):
        cleaned = re.sub(r"[\s:,\-]+", "", text)
        if len(cleaned) > 0 and _HAS_DIGIT.search(cleaned):
            return True

    return False


def _is_clear_label(span: TextSpan) -> bool:
    text = span.text.strip()
    if not text or not _LATIN.search(text):
        return False

    clean = text.rstrip(":").strip()
    if _is_keep_token(clean) or clean.upper() in KEEP_TOKENS:
        return False
    if re.fullmatch(r"(?:CGST|SGST|UGST|IGST|GST)(?:\s*/\s*(?:CGST|SGST|UGST|IGST|GST))*", clean, re.I):
        return False

    # Never treat pure names / long refs as labels
    if _looks_like_person_name(text) or _looks_like_long_reference(text):
        return False

    if _is_banking_label(text):
        return True

    # Very common short document titles / section headers
    lower = clean.lower()
    common_headers = {
        "tax invoice", "invoice", "admit card", "receipt", "statement",
        "sold by", "bill to", "ship to", "billing address", "shipping address",
        "gst summary", "coupon code", "item code", "serial number",
        "prepared by", "verified by", "authorised signature", "authorized signature",
        "total", "rate", "amt", "qty", "discount", "value", "taxable",
        "unit price", "cod charge", "shipping charge", "grand total",
        "total amount", "total tax", "store credit", "gv value",
        "order type", "payment mode", "type of supply", "place of supply",
        "order currency", "goods and services", "prepaid",
        "s.no", "sl.no", "sr.no", "hsn", "sac",
    }
    if lower in common_headers:
        return True

    # Short bold text without digits often a header
    if "bold" in span.font_name.lower() and len(text.split()) <= 6 and not _HAS_DIGIT.search(text):
        return True

    # Short text ending with colon is almost always a label
    if text.endswith(":") and len(text) <= 50 and not _HAS_DIGIT.search(text):
        return True

    return False


# ---------------------------------------------------------- span handling ----

def _split_parts(text: str, ranges: List[Tuple[int, int]], value_map: Dict[str, str], counter: List[int]) -> List[ProtectedPart]:
    parts: List[ProtectedPart] = []
    pos = 0
    for a, b in ranges:
        if a > pos:
            parts.append(ProtectedPart(text=text[pos:a], is_protected=False))
        counter[0] += 1
        ph = f"{{{{VAL_{counter[0]}}}}}"
        value_map[ph] = text[a:b]
        parts.append(ProtectedPart(text=text[a:b], is_protected=True, placeholder=ph))
        pos = b
    if pos < len(text):
        parts.append(ProtectedPart(text=text[pos:], is_protected=False))
    return parts


def _protect_whole(span: TextSpan, value_map: Dict[str, str], counter: List[int]) -> ProtectedSpan:
    counter[0] += 1
    ph = f"{{{{VAL_{counter[0]}}}}}"
    value_map[ph] = span.text
    return ProtectedSpan(
        original_span=span,
        parts=[ProtectedPart(text=span.text, is_protected=True, placeholder=ph)],
        final_text_for_translation=ph,
        units=[],
        template="",
        uses_placeholders=False,
    )


def _make_label_only(span: TextSpan, label_text: str) -> ProtectedSpan:
    return ProtectedSpan(
        original_span=span,
        parts=[ProtectedPart(text=span.text, is_protected=False)],
        final_text_for_translation=label_text,
        units=[label_text],
        template="{T0}",
        uses_placeholders=False,
    )


def _neutralise_no(text: str) -> str:
    """Prevent the model from translating 'No' as 'नहीं'."""
    text = re.sub(r"\bNo\.?\b", "Number", text, flags=re.I)
    text = re.sub(r"\bSl\.?\s*No\.?\b", "Serial Number", text, flags=re.I)
    text = re.sub(r"\bS\.?\s*No\.?\b", "Serial Number", text, flags=re.I)
    return text


def protect_span(span: TextSpan, value_map: Dict[str, str], counter: List[int]) -> ProtectedSpan:
    text = span.text
    stripped = text.strip()

    # 1. Nothing translatable
    if not _LATIN.search(text) or stripped.lower() in KEEP_PHRASES:
        return _protect_whole(span, value_map, counter)

    # 2. Clear Value → protect everything
    if _is_clear_value(span):
        return _protect_whole(span, value_map, counter)

    # 3. Clear Label → translate
    if _is_clear_label(span):
        safe = _neutralise_no(stripped)
        return _make_label_only(span, safe)

    # 4. Terms & Conditions / long instructions → translate
    if _is_terms_or_instruction(text):
        safe = _neutralise_no(stripped)
        return _make_label_only(span, safe)

    # 5. Mixed or uncertain → find value ranges and protect them
    ranges = _find_value_ranges(text)
    parts = _split_parts(text, ranges, value_map, counter)

    letter_idx = [i for i, p in enumerate(parts)
                  if not p.is_protected and _LATIN.search(p.text)]

    if not letter_idx:
        for p in parts:
            if p.placeholder:
                value_map.pop(p.placeholder, None)
        return _protect_whole(span, value_map, counter)

    def ph_or_text(p: ProtectedPart) -> str:
        return p.placeholder if p.is_protected else p.text

    cores = {}
    for i in letter_idx:
        seg = parts[i].text
        core = seg.strip(_EDGE_CHARS)
        cores[i] = core

    prose = any(len(c.split()) >= 5 for c in cores.values())

    if len(letter_idx) > 1 and prose:
        sent = "".join(ph_or_text(p) for p in parts)
        sent = _neutralise_no(sent)
        return ProtectedSpan(
            original_span=span,
            parts=parts,
            final_text_for_translation=sent,
            units=[sent],
            template="{T0}",
            uses_placeholders=True,
        )

    # Label + value style
    units, template = [], []
    k = 0
    for i, p in enumerate(parts):
        if i in cores:
            seg = p.text
            lead = seg[: len(seg) - len(seg.lstrip(_EDGE_CHARS))]
            trail = seg[len(seg.rstrip(_EDGE_CHARS)):]
            core = _neutralise_no(cores[i])
            units.append(core)
            template.append(f"{lead}{{T{k}}}{trail}")
            k += 1
        else:
            template.append(ph_or_text(p))

    return ProtectedSpan(
        original_span=span,
        parts=parts,
        final_text_for_translation=" | ".join(units),
        units=units,
        template="".join(template),
        uses_placeholders=False,
    )


# ---------------------------------------------------------- public API -------

def protect_document(document: DocumentData) -> ProtectionResult:
    value_map: Dict[str, str] = {}
    protected_spans: List[ProtectedSpan] = []
    counter = [0]
    stats = {
        "total_spans": 0,
        "spans_with_protection": 0,
        "total_protected_values": 0,
        "fully_static_spans": 0,
        "fully_protected_spans": 0,
        "translation_units": 0,
    }

    for page in document.pages:
        for span in page.spans:
            stats["total_spans"] += 1
            ps = protect_span(span, value_map, counter)
            protected_spans.append(ps)

            n_prot = sum(1 for p in ps.parts if p.is_protected)
            if not ps.units:
                stats["fully_protected_spans"] += 1
            elif n_prot:
                stats["spans_with_protection"] += 1
            else:
                stats["fully_static_spans"] += 1
            stats["total_protected_values"] += n_prot
            stats["translation_units"] += len(ps.units)

    return ProtectionResult(document, protected_spans, value_map, stats)


def print_protection_report(result: ProtectionResult) -> None:
    s = result.stats
    print("=" * 70)
    print("PROTECTION REPORT (Banking-focused)")
    print("=" * 70)
    print(f"Total spans            : {s['total_spans']}")
    print(f"Fully protected (kept) : {s['fully_protected_spans']}")
    print(f"Label + value spans    : {s['spans_with_protection']}")
    print(f"Pure translatable spans: {s['fully_static_spans']}")
    print(f"Translation units      : {s['translation_units']}")
    print(f"Protected values       : {s['total_protected_values']}")
    print("-" * 70)
    print("[WHAT THE TRANSLATOR WILL SEE]")
    shown = 0
    for ps in result.protected_spans:
        if ps.units and shown < 50:
            print(f"  {ps.original_span.text[:58]!r:60} -> {ps.units}")
            shown += 1
    print("=" * 70)