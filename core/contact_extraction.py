"""Parse contact fields from locally OCR-recognized text on images or video frames."""
import re

EMAIL = re.compile(r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b")
URL = re.compile(r"(?i)\b(?:https?://|www\.)[a-z0-9.-]+(?:\.[a-z]{2,})(?:[/\w.?&=%+#-]*)?")
DOMAIN = re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:ru|com|net|org|рф|io|tech|pro)\b")
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")


def extract_contacts_from_text(text):
    text = str(text or "")
    emails = list(dict.fromkeys(EMAIL.findall(text)))
    urls = list(dict.fromkeys(URL.findall(text)))
    domains = [x for x in dict.fromkeys(DOMAIN.findall(text)) if x.lower() not in {u.lower() for u in urls}]
    phones = []
    for raw in PHONE.findall(text):
        digits = re.sub(r"\D", "", raw)
        has_label = bool(re.search(r"(?i)(?:тел(?:ефон)?|phone|mobile|моб\.)[^\n]{0,32}" + re.escape(raw), text))
        is_international = (raw.strip().startswith("+") and len(digits) in (10, 11, 12, 13, 14, 15)) or (len(digits) == 11 and digits.startswith(("7", "8")))
        looks_like_date_or_time = bool(re.search(r"\b\d{1,2}-\d{2}\s+\d{1,2}-\d{2}-\d{4}\b", raw))
        if len(digits) >= 9 and (has_label or is_international) and not looks_like_date_or_time:
            normalized = re.sub(r"\s+", " ", raw).strip()
            if normalized not in phones:
                phones.append(normalized)
    return {"email": emails, "phone": phones, "website": list(dict.fromkeys(urls + domains))}


def has_contacts(value):
    contacts = value if isinstance(value, dict) else extract_contacts_from_text(value)
    return any(contacts.get(key) for key in ("email", "phone", "website"))
