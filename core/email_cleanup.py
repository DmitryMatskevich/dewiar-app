"""Local email cleanup rules shared by the CLI cleaner and Dewiar Studio.

Rules identify only the categories explicitly requested by the user. The caller
decides whether to omit matches from a derived archive or create a filtered copy;
this module never deletes files or communicates with a mail server.
"""
from __future__ import annotations

import re
from email.header import decode_header, make_header
from email.utils import parseaddr


def _decoded(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


HOSTS = {
    "nic.ru": "хостинг и домены (RU-CENTER)", "firstvds.ru": "хостинг (FirstVDS)",
    "cloud.yandex.ru": "облачный провайдер (Yandex Cloud)", "mail.k2.cloud": "облачный провайдер (K2 Cloud)",
    "k2.cloud": "облачный провайдер (K2 Cloud)", "email.cloud.mts.ru": "облачный провайдер (MTS Cloud)",
    "cloud.ru": "облачный провайдер (Cloud.ru)", "sbercloud.ru": "облачный провайдер (SberCloud)",
    "qazcloud.kz": "облачный провайдер (QazCloud)", "selectel.ru": "хостинг (Selectel)",
    "beget.ru": "хостинг (Beget)", "beget.com": "хостинг (Beget)",
    "timeweb.ru": "хостинг (Timeweb)", "timeweb.cloud": "хостинг (Timeweb)",
    "reg.ru": "хостинг и домены (REG.RU)", "hoster.ru": "хостинг",
}
BANKS = {
    "alfabank.ru": "банк (Альфа-Банк)", "alfa-bank.info": "банк (Альфа-Банк)",
    "alfacapital.ru": "банк (Альфа-Капитал)", "emails.tinkoff.ru": "банк (Т-Банк)",
    "emails.tbank.ru": "банк (Т-Банк)", "tbank.ru": "банк (Т-Банк)",
    "gazprombank.ru": "банк (Газпромбанк)", "tochka.com": "банк (Точка)",
    "sberbank.ru": "банк (Сбербанк)", "vtb.ru": "банк (ВТБ)",
    "raiffeisen.ru": "банк (Райффайзен)", "modulbank.ru": "банк (Модульбанк)",
    "open.ru": "банк (Открытие)", "ozon.ru": "банк (Озон Банк)",
}
STORES = {
    "jumboretail.ae": "магазин (Jumbo Electronics)", "jumbo.ae": "магазин (Jumbo Electronics)",
    "flowwow.com": "магазин (Flowwow)", "alpinabook.ru": "магазин (Альпина.Книги)",
    "letter.movavi.com": "магазинная рассылка (Movavi)", "promo.movavi.com": "магазин (Movavi)",
    "promo.aliexpress.ru": "магазин (AliExpress)", "techstreet.com": "магазин",
    "spiritofshopping.com": "магазинная рассылка/спам", "newtechmarket.sa.com": "магазинная рассылка/спам",
}
AIR_TRAVEL = {
    "aeroflot.ru": "авиакомпания (Аэрофлот)", "airastana.com": "авиакомпания (Air Astana)",
    "pobeda.aero": "авиакомпания (Победа)", "flypobeda.ru": "авиакомпания (Победа)",
    "s7.ru": "авиакомпания (S7)", "airarabia.com": "авиакомпания (Air Arabia)",
    "uzairways.com": "авиакомпания (Uzbekistan Airways)", "onetwotrip.com": "сервис бронирования поездок",
    "booking.com": "отель и бронирование (Booking.com)", "hotelandes.com": "отель",
    "genzianella.com": "отель и отдых", "aero-express.ru": "транспорт в аэропорт",
}
SOCIAL = ("linkedin.com", "instagram.com", "facebookmail.com", "facebook.com", "meta.com", "threads.net",
          "discord.com", "youtube.com", "reddit.com", "tiktok.com", "twitter.com", "x.com", "pinterest.com",
          "vk.com", "vkontakte.ru", "telegram.org")
EVENT_SUBJECT = re.compile(
    r"конференц|форум|forum|summit|conference|congress|конгресс|вебинар|webinar|семинар|выставк|"
    r"фестиваль|симпозиум|саммит|митап|meetup", re.I
)
PAYMENT_CONFIRMATION = re.compile(
    r"робокасс|robokassa|подтверждени[ея]\s+оплат|оплат[а-яё]*\s+(?:успешно|прошл[ао]|подтвержден[ао]?|получен[ао]?)|"
    r"плат[её]ж\s+(?:успешно|прош[её]л|подтвержден[ао]?|получен[ао]?)|чек\s+об\s+оплат|квитанци[яи]\s+об\s+оплат|"
    r"payment\s+(?:confirmation|received|successful|receipt)|your\s+payment\s+(?:was\s+)?(?:successful|confirmed)", re.I
)
BANK_NAME = re.compile(
    r"альфа.?банк|альфа.?инвест|альфа.?партн|альфа.?онли|т.?банк|тинькофф|газпромбанк|"
    r"банк точка|документооборот точки|сбербанк|втб|райффайзен|модульбанк|озон банк|банк открытие", re.I
)


def _domain(address: str) -> str:
    address = parseaddr(address or "")[1].lower()
    return address.rsplit("@", 1)[-1] if "@" in address else ""


def _match_domain(domain: str, mapping: dict[str, str]) -> str | None:
    for suffix, label in mapping.items():
        if domain == suffix or domain.endswith("." + suffix):
            return label
    return None


def classify(message) -> list[str]:
    """Return cleanup reasons. An empty list means keep the message."""
    sender = _decoded(message.get("From", ""))
    subject = _decoded(message.get("Subject", ""))
    domain = _domain(sender)
    reasons = []
    if "github" in domain or "github" in sender.lower():
        reasons.append("GitHub")
    if domain == "google.com" and "googlealerts-noreply" in sender.lower():
        reasons.append("Google Alerts")
    if any(domain == s or domain.endswith("." + s) for s in SOCIAL):
        reasons.append("социальная сеть")
    reason = _match_domain(domain, STORES)
    if reason:
        reasons.append(reason)
    # Known retail impersonation/spam domains from the user's current archive.
    if any(x in domain for x in ("rumixos.shop", "onebestitems.shop", "kionwahgajca.shop", "kabillaraos.shop",
                                  "gifts401.xyz", "genosdentro.ru", "alwavesdams.ru", "daudpro.ru",
                                  "gethebest.shop", "aipromos.click", "bestsitems.store", "bestoyou.click",
                                  "gooostops.click", "thebestmarket.click")):
        reasons.append("магазинная рассылка/спам")
    if re.search(r"\b(temu|aliexpress|ali_express|dn\$ store|dns customer first team)\b", sender, re.I):
        reasons.append("магазинная рассылка")
    if domain in ("ein.daudpro.ru", "alwavesdams.ru", "genosdentro.ru"):
        reasons.append("магазинная рассылка/спам")
    reason = _match_domain(domain, AIR_TRAVEL)
    if reason:
        reasons.append(reason)
    if "emails.tinkoff.ru" in domain and re.search(r"путешеств|полет|полёт|рейс|тур|авиабилет|s7 airlines|колес", subject, re.I):
        reasons.append("путешествия (Т-Банк)")
    if "oppositionvat.best" in domain and re.search(r"vacation|family photos", subject, re.I):
        reasons.append("отдых/туризм (спам)")
    if _match_domain(domain, HOSTS):
        reasons.append(_match_domain(domain, HOSTS))
    if any(x in sender.lower() for x in ("ru-center", "руцентр", "firstvds", "yandex cloud", "yandex neuro scale")):
        reasons.append("хостинг и облачные услуги")
    reason = _match_domain(domain, BANKS)
    if reason:
        reasons.append(reason)
    elif BANK_NAME.search(sender):
        reasons.append("банк (распознанное имя отправителя)")
    if "megafon" in sender.lower() or "мегафон" in sender.lower():
        reasons.append("МегаФон")
    if "datacenterdynamics.com" in domain or re.search(r"\bdcd\b", sender, re.I):
        reasons.append("DCD")
    if re.search(r"rubezh|ru.?беж|рубеж|ru-bezh", sender.lower()):
        reasons.append("журнал RUБЕЖ")
    if "webmaster" in sender.lower() and ("yandex" in sender.lower() or "яндекс" in sender.lower()):
        reasons.append("Yandex Webmaster")
    if "gitex" in sender.lower() or "gitex" in subject.lower():
        reasons.append("GITEX")
    if "cloudkassir" in sender.lower() or "cloudkassir" in domain:
        reasons.append("CloudKassir")
    if "yandex go" in sender.lower() or domain in ("taxi.yandex.ru", "yandexgo.ru"):
        reasons.append("Яндекс Go")
    if re.search(r"толстобров|толстоборов|tolstobrov|tolstoborov", sender.lower()):
        reasons.append("Толстоброва")
    if PAYMENT_CONFIRMATION.search(sender) or PAYMENT_CONFIRMATION.search(subject):
        reasons.append("подтверждение оплаты")
    if "ofd.ru" in domain and re.search(r"кассовый чек|чек\s+(?:от|\d)", subject, re.I):
        reasons.append("подтверждение оплаты")
    if "hostobzor.ru" in domain and re.search(r"hostобзор|хостобзор", sender + " " + subject, re.I):
        reasons.append("конференция/форум/мероприятие")
    if domain.startswith("event."):
        reasons.append("конференция/форум/мероприятие")
    if any(x in sender.lower() or x in domain for x in (
        "networking+", "denyanmedia.com", "globalcio.com", "cablinginstall.com",
        "lightwaveonline.com", "comnews.ru", "ru-bezh.ru"
    )):
        reasons.append("информационная подписка")
    if re.search(r"newsletter|weekly|monthly review|daily news|news digest|magazine", subject, re.I):
        reasons.append("информационная подписка")
    # Replies/forwards from individuals can be real correspondence about an event;
    # leave those for human review instead of treating them like event marketing.
    if EVENT_SUBJECT.search(subject) and not re.match(r"^\s*(?:re|fw|fwd)\s*:", subject, re.I):
        reasons.append("конференция/форум/мероприятие")
    return list(dict.fromkeys(reasons))
