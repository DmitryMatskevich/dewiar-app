"""Public price lookup. No credentials, inference requests, or routing changes."""
import datetime as dt
from decimal import Decimal, InvalidOperation
import json
import math
import threading
from pathlib import Path
import httpx

URL = 'https://openrouter.ai/api/v1/models'
FAMILIES = {'openai':'OpenAI', 'anthropic':'Claude', 'deepseek':'DeepSeek', 'inclusionai':'Ling'}
LOCK = threading.Lock()
SOURCES = [
    {'name':'Тарифы OpenRouter', 'url':'https://openrouter.ai/models'},
    {'name':'OpenAI напрямую', 'url':'https://developers.openai.com/api/docs/pricing'},
    {'name':'Claude напрямую', 'url':'https://platform.claude.com/docs/en/about-claude/pricing'},
    {'name':'DeepSeek напрямую', 'url':'https://api-docs.deepseek.com/quick_start/pricing/'},
]


def rate(value):
    try:
        result = float(Decimal(str(value)) * 1_000_000)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError, OverflowError, InvalidOperation):return None


def normalize(data):
    if not isinstance(data, list):raise ValueError('Нет каталога моделей')
    rows = []
    for item in data:
        if not isinstance(item, dict):continue
        ident = item.get('id', '')
        family = ident.split('/')[0]
        if family not in FAMILIES or ':' in ident:continue  # realtime standard catalog, no batch/fast mix
        pricing = item.get('pricing') or {}
        if rate(pricing.get('prompt')) is None or rate(pricing.get('completion')) is None:continue
        rows.append({'id':ident, 'name':item.get('name') or ident, 'provider':FAMILIES[family],
                     'context':item.get('context_length'), 'pricing':pricing,
                     'url':'https://openrouter.ai/' + ident})
    if not rows:raise ValueError('В каталоге не найдены тарифы')
    return rows


def matches(rule, tokens, now):
    constraints = set(rule) - {'prompt','completion','input_cache_read','input_cache_write','input_cache_write_1h'}
    if constraints - {'min_prompt_tokens','max_prompt_tokens','utc_days','utc_start','utc_end'}:
        return None
    if 'min_prompt_tokens' in rule and tokens < rule['min_prompt_tokens']:return False
    if 'max_prompt_tokens' in rule and tokens > rule['max_prompt_tokens']:return False
    days = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
    if 'utc_days' in rule and days[now.weekday()] not in rule['utc_days']:return False
    if 'utc_start' in rule or 'utc_end' in rule:
        minute = now.hour * 60 + now.minute
        start = (rule.get('utc_start',0)//100)*60 + rule.get('utc_start',0)%100
        endval = rule.get('utc_end',0); end = (endval//100)*60 + endval%100
        if end == 0:end = 1440
        if not start <= minute < end:return False
    return True


def estimate(row, input_tokens, output_tokens, now):
    pricing = dict(row['pricing']); conditional = bool(pricing.get('overrides'))
    unknown = False
    for rule in pricing.get('overrides') or []:
        match = matches(rule, input_tokens, now)
        if match is None:unknown = True
        elif match:pricing.update(rule)
    too_long = bool(row.get('context') and input_tokens + output_tokens > row['context'])
    if too_long:unknown = True
    inp, out, cache = (rate(pricing.get(k)) for k in ('prompt','completion','input_cache_read'))
    return {k:v for k,v in row.items() if k != 'pricing'} | {
        'input':inp, 'output':out, 'cache':cache, 'conditional':conditional,
        'cost':None if unknown else (input_tokens * inp + output_tokens * out) / 1_000_000,
        'note':'Запрос и ответ превышают контекст модели.' if too_long else
               'Есть условия тарифа, требующие проверки на странице модели.' if unknown else
               'Условия времени и длины запроса учтены по каталогу.' if conditional else ''}


def get_prices(state_dir, refresh=False, input_tokens=10000, output_tokens=1000, now=None):
    import egress
    from settings import DATA_DIR
    for value in (input_tokens, output_tokens):
        if isinstance(value, bool) or not isinstance(value,int) or not 0 <= value <= 10_000_000:
            raise ValueError('Укажите от 0 до 10 000 000 токенов')
    now = now or dt.datetime.now(dt.timezone.utc)
    path = Path(state_dir) / 'model-prices.json'
    cached, warning = None, ''
    with LOCK:
        for candidate in (path, Path(DATA_DIR) / 'model-prices.json'):
            try:
                value = json.loads(candidate.read_text())
                if value.get('models') and value.get('checked_at'):cached = value;break
            except (OSError, ValueError, AttributeError):pass
        if refresh and not egress.local_only():
            try:
                response = httpx.get(URL, timeout=15, follow_redirects=False)
                response.raise_for_status()
                rows = normalize(response.json().get('data'))
                cached = {'models':rows, 'checked_at':now.isoformat()}
                try:
                    path.parent.mkdir(parents=True,exist_ok=True)
                    temp = path.with_suffix('.tmp');temp.write_text(json.dumps(cached,ensure_ascii=False));temp.replace(path)
                except OSError:warning = 'Тарифы получены, но сохранить их на диск не удалось.'
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                warning = 'Не удалось проверить цены в интернете. Показана сохранённая таблица; тарифы могли измениться.'
        elif refresh:
            warning = 'Включён режим без интернета. Показана сохранённая таблица.'
    if not cached:raise ValueError('Тарифы пока недоступны. Подключитесь к интернету и повторите проверку.')
    rows = [estimate(r,input_tokens,output_tokens,now) for r in cached['models']]
    rows.sort(key=lambda r:(r['cost'] is None,r['cost'] or 0,r['name']))
    return {'models':rows,'checked_at':cached['checked_at'],'calculated_at':now.isoformat(),
            'warning':warning,'sources':SOURCES,'source':'OpenRouter · стандартный API · USD',
            'input_tokens':input_tokens,'output_tokens':output_tokens}
