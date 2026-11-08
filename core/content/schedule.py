"""Explicit UTC scheduling; reject nonexistent/ambiguous local times."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

def zone(name):
    if not isinstance(name,str) or len(name)>100:raise ValueError('Укажите часовой пояс IANA')
    try:return ZoneInfo(name)
    except (ValueError,ZoneInfoNotFoundError) as e:raise ValueError('Неизвестный часовой пояс IANA') from e

def instant(value,timezone_name):
    zone(timezone_name)
    if not isinstance(value,str):raise ValueError('Нужны дата и время с UTC offset')
    try:d=datetime.fromisoformat(value)
    except ValueError as e:raise ValueError('Некорректная дата расписания') from e
    if d.tzinfo is None:raise ValueError('Укажите UTC offset расписания')
    return d.astimezone(timezone.utc).timestamp()

def from_local(value,timezone_name,fold=None):
    tz=zone(timezone_name)
    try:d=datetime.fromisoformat(value)
    except (ValueError,TypeError) as e:raise ValueError('Некорректная местная дата') from e
    if d.tzinfo is not None:raise ValueError('Местная дата должна быть без UTC offset')
    variants=[]
    for f in (0,1):
        candidate=d.replace(tzinfo=tz,fold=f);back=candidate.astimezone(timezone.utc).astimezone(tz)
        if back.replace(tzinfo=None)==d and all(v.utcoffset()!=candidate.utcoffset() for v in variants):variants.append(candidate)
    if not variants:raise ValueError('Это местное время не существует из-за перехода часов')
    if fold is not None and (type(fold) is not int or fold not in (0,1)):raise ValueError('fold должен быть 0 или 1')
    if len(variants)>1 and fold is None:raise ValueError('Неоднозначное местное время; выберите первое или второе вхождение')
    result=next((v for v in variants if v.fold==(fold or 0)),variants[0])
    return result.astimezone(timezone.utc).isoformat()
