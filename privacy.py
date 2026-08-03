"""
DLP masking for the Sandbox API — it rejects requests containing personal data with
HTTP 400 {"error": "HAS_PERSONAL_DATA"}. Real dialogues carry names, phone numbers and
card fragments, so every outgoing message is masked before send.

Ported from the colleague's analytics_tool package (llm.py), adapted for this repo:
- `mask(text)`            — standard pass: emails/urls → tokens, card expiry → xx/xx,
                            13–19-digit runs → x, CVV/OTP/код words and card phrases →
                            neutral wording, ALL-CAPS cardholder names → lowercase.
- `mask(text, strict=True)` — last-resort pass for the automatic retry: additionally
                            every digit → x and sensitive topic words → euphemisms.
- `mask_messages(...)`    — applies mask() to user/assistant/tool content. `system` is
                            NEVER masked: our prompts hold no client data, and the agent
                            under test must run its production prompt verbatim.
- `is_personal_data_error(text)` — recognizes the Sandbox DLP rejection in an error body.

Wiring: per-role `sanitize = true` in agents.toml (shipped on for Sandbox roles) makes
oai.py mask every request and retry once with strict=True on a DLP rejection.

Deviation from the source: whitespace is collapsed per line, newlines are kept — the
judged dialogues are multiline CLIENT/OPERATOR transcripts and must stay readable.
"""
import re

# ── what the Sandbox DLP reacts to (patterns from the analytics_tool package) ──
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+", re.I)
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_CARDHOLDER = re.compile(r"\b[A-Z'\-]{2,26} [A-Z'\-]{1,26}\b")   # IVAN IVANOV on a card
_CARDHOLDER_RU = re.compile(r"\b[А-ЯЁ'\-]{2,26} [А-ЯЁ'\-]{1,26}\b")  # ИВАН ПЕТРОВ (our dialogues
                                                                     # are Russian — source only
                                                                     # covered the latin card form)
_CARD_EXPIRY = re.compile(r"\b(?:0[1-9]|1[0-2])/\d{2}\b")
_LONG_DIGITS = re.compile(r"\b\d{13,19}\b")                       # card / account numbers
_ANY_DIGIT = re.compile(r"\d")

# (pattern, replacement) applied in order; case-insensitive
_WORD_MASKS = [
    (re.compile(r"\b(?:CAV2|CVC2|CVV2|CID|CVV|CVC|OTP)\b", re.I), "проверка"),
    (re.compile(r"\bcode\b", re.I), "techmark"),
    (re.compile(r"\bкод(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b", re.I), "техметка"),
    (re.compile(r"\bномер\s+карт\w*\b", re.I), "номер продукта"),
    (re.compile(r"\b(?:плат[её]жн\w*|банковск\w*)\s+карт\w*\b", re.I), "банковский продукт"),
    (re.compile(r"\b(?:credit|debit|payment)\s+card\b", re.I), "payment product"),
    (re.compile(r"\b(?:плат[её]жн\w*\s+)?реквизит\w*(?:\s+карт\w*)?\b", re.I), "платежные данные"),
    (re.compile(r"\bсрок\s+действия\s+карт\w*\b", re.I), "срок продукта"),
    (re.compile(r"\bдата\s+окончания\s+действия\b", re.I), "дата окончания"),
    (re.compile(r"\b(?:код\s+)?PIN\b", re.I), "пин"),
    (re.compile(r"\b(?:qr|куар|кьюар)\s*[- ]?\s*код\w*\b", re.I), "qr"),
    (re.compile(r"\b(?:пин|pin)\s*[- ]?\s*код\w*\b", re.I), "пин"),
    (re.compile(r"\b(?:sms|смс)\s*[- ]?\s*код\w*\b", re.I), "смс"),
]

# strict-only: topic euphemisms the Sandbox DLP is known to flag in context
_STRICT_MASKS = [
    (re.compile(r"\bФНС\b", re.I), "госорган"),
    (re.compile(r"\bсудебн\w*\b", re.I), "правов"),
    (re.compile(r"\bарест\w*\b", re.I), "ограничение"),
    (re.compile(r"\bсчет(ах|а|ов|ом|е|у|ы)?\b", re.I), "банковский продукт"),
    (re.compile(r"\bзадолженн\w*\b", re.I), "долг"),
    (re.compile(r"\bпросроч\w*\b", re.I), "просрочка"),
    (re.compile(r"\bстраховк\w*\b", re.I), "дополнительная услуга"),
]


def _x_digits(match: re.Match) -> str:
    return _ANY_DIGIT.sub("x", match.group(0))


def mask(text: str, strict: bool = False) -> str:
    """Mask personal-data triggers; strict=True is the aggressive retry variant."""
    safe = str(text)
    safe = _EMAIL.sub("email", safe)
    safe = _URL.sub("url", safe)
    safe = _CARDHOLDER.sub(lambda m: m.group(0).lower(), safe)
    safe = _CARDHOLDER_RU.sub(lambda m: m.group(0).lower(), safe)
    for pattern, replacement in _WORD_MASKS:
        safe = pattern.sub(replacement, safe)
    safe = _CARD_EXPIRY.sub("xx/xx", safe)
    safe = _LONG_DIGITS.sub(_x_digits, safe)
    if strict:
        safe = _ANY_DIGIT.sub("x", safe)
        for pattern, replacement in _STRICT_MASKS:
            safe = pattern.sub(replacement, safe)
    # collapse runs of spaces/tabs but KEEP newlines — dialogues are multiline transcripts
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in safe.splitlines()).strip()


def mask_messages(messages: list[dict], strict: bool = False) -> list[dict]:
    """mask() every message's text content; `system` passes through verbatim."""
    out = []
    for message in messages:
        content = message.get("content")
        if message.get("role") == "system" or not isinstance(content, str):
            out.append(message)
        else:
            out.append({**message, "content": mask(content, strict)})
    return out


def is_personal_data_error(text: str) -> bool:
    low = str(text).lower()
    return "has_personal_data" in low or "personal data is found" in low
