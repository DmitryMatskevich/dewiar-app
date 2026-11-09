"""Editable literal voice templates. Parsing/preview never executes an action."""
import copy
import re


def norm(value):
    return ' '.join(str(value).casefold().replace('ё', 'е').strip(' «»"„“.,!?').split())


def action(key, title, verbs, slot='приложение'):
    return {'id': key, 'title': title, 'enabled': True,
            'phrases': [v if '{' in v else v + ' {' + slot + '}' for v in verbs]}


DEFAULT = {
    'defaults_revision': 3,
    'actions': [
        action('open', 'Открыть приложение', ['открой', 'открыть', 'отрыть', 'запусти', 'запустить']),
        action('quit', 'Закрыть приложение', ['закрой', 'закрыть', 'заверши', 'завершить', 'отключи', 'отключить', 'выключи', 'выключить']),
        action('minimize', 'Свернуть приложение', ['сверни', 'свернуть']),
        action('focus', 'Переключиться на приложение', ['переключись на', 'переключиться на', 'перейди в', 'активируй', 'активировать',
               'покажи приложение', 'переведи фокус на', 'переключи фокус на', 'установи фокус на', 'фокус на', 'фокус', 'активная на', 'активное на',
               'сделай {приложение} активной', 'сделай {приложение} активным', 'сделай активной {приложение}', 'сделай активным {приложение}']),
        {'id': 'dnd_on', 'title': 'Включить «Не беспокоить» на Mac', 'enabled': True,
         'phrases': ['не беспокоить', 'переговоры', 'включи не беспокоить', 'включи режим не беспокоить', 'включи фокусировку не беспокоить']},
        {'id': 'dnd_off', 'title': 'Выключить «Не беспокоить» на Mac', 'enabled': True,
         'phrases': ['выключи не беспокоить', 'выключи режим не беспокоить', 'отключи не беспокоить', 'отключи режим не беспокоить']},
        action('song_process', 'Добавить песню и запустить пакетную обработку',
               ['найти песню', 'поиск песни', 'поискать песню', 'обработать песню', 'обработай песню'], 'запрос'),
        action('yandex_search', 'Найти песню в Яндекс Музыке', ['найди песню', 'открой песню', 'включи песню', 'найди трек', 'включи трек'], 'запрос'),
    ],
    'applications': [
        {'name': 'Claude', 'target': 'Claude', 'aliases': ['клод', 'клод код']},
        {'name': 'Codex', 'target': 'com.openai.codex', 'aliases': ['codex', 'кодекс', 'чатджипити', 'джипити']},
        {'name': 'Dewiar', 'target': 'Dewiar', 'aliases': ['девиар', 'агент', 'ассистент', 'помощник']},
        {'name': 'Cursor', 'target': 'Cursor', 'aliases': ['курсор', 'редактор код']},
        {'name': 'Yandex Music', 'target': 'ru.yandex.desktop.music', 'aliases': ['yandex music', 'яндекс музыка', 'яндекс музыку', 'музыка', 'музыку', 'песни']},
        {'name': 'WhatsApp', 'target': 'WhatsApp', 'aliases': ['WhatsApp']},
        {'name': 'DeepSeek Harness', 'target': 'DeepSeek Harness', 'aliases': ['дипсиик']},
        {'name': 'Visual Studio Code', 'target': 'Code', 'aliases': ['вскод', 'вс код', 'visual studio code']},
        {'name': 'Safari', 'target': 'Safari', 'aliases': ['сафари']},
        {'name': 'Telegram', 'target': 'Telegram', 'aliases': ['телеграм', 'телеграмм']},
        {'name': 'Google Chrome', 'target': 'Google Chrome', 'aliases': ['хром', 'гугл хром']},
        {'name': 'Calculator', 'target': 'Calculator', 'aliases': ['калькулятор']},
        {'name': 'Terminal', 'target': 'Terminal', 'aliases': ['терминал']},
        {'name': 'Notes', 'target': 'Notes', 'aliases': ['заметки']},
        {'name': 'Calendar', 'target': 'Calendar', 'aliases': ['календарь']},
        {'name': 'Numbers', 'target': 'com.apple.iWork.Numbers', 'aliases': ['таблица', 'таблицу', 'таблицы', 'намберс']},
        {'name': 'LibreOffice', 'target': 'org.libreoffice.script', 'aliases': ['офис', 'либреофис', 'либре офис', 'linbreoffice']},
        {'name': 'Mail', 'target': 'com.apple.mail', 'aliases': ['почта', 'почту']},
        {'name': 'Contacts', 'target': 'com.apple.AddressBook', 'aliases': ['контакты', 'контакт', 'контактов']},
        {'name': 'TextEdit', 'target': 'TextEdit', 'aliases': ['текстэдит', 'текстовый редактор']},
        {'name': 'Finder', 'target': 'Finder', 'aliases': ['файндер']},
        {'name': 'System Settings', 'target': 'System Settings', 'aliases': ['настройки системы']},
        {'name': 'Yandex', 'target': 'Yandex', 'aliases': ['яндекс браузер', 'яндекс']},
        {'name': 'Ollama', 'target': 'Ollama', 'aliases': ['оллама']},
        {'name': 'Strata', 'target': 'Strata', 'aliases': ['страта']},
    ],
}
IDS = {a['id'] for a in DEFAULT['actions']}
LITERAL_ACTIONS = {'dnd_on', 'dnd_off'}
QUERY_ACTIONS = {'yandex_search', 'song_process'}
NEW_APPLICATIONS = {'com.apple.iWork.Numbers', 'org.libreoffice.script', 'com.apple.mail', 'com.apple.AddressBook'}


def config(value=None):
    result = copy.deepcopy(DEFAULT)
    if isinstance(value, dict):
        for key in ('actions', 'applications'):
            if key in value:
                result[key] = copy.deepcopy(value[key])
        # Add only newly introduced defaults to old saved editors. Never restore
        # removed aliases of older apps or replace a user's existing assignment.
        revision = value.get('defaults_revision', 1)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError('Неверная версия настроек голосовых команд')
        if revision < 2:
            if isinstance(result['actions'], list) and all(isinstance(a, dict) for a in result['actions']):
                known = {a.get('id') for a in result['actions']}
                result['actions'].extend(copy.deepcopy(a) for a in DEFAULT['actions'] if a['id'] in LITERAL_ACTIONS and a['id'] not in known)
            if isinstance(result['applications'], list) and all(isinstance(a, dict) for a in result['applications']):
                used = {norm(x) for a in result['applications'] for x in [a.get('name', ''), a.get('target', '')] + (a.get('aliases') if isinstance(a.get('aliases'), list) else [])}
                for a in DEFAULT['applications']:
                    labels = {norm(x) for x in [a['name'], a['target']] + a['aliases']}
                    if a['target'] in NEW_APPLICATIONS and not labels.intersection(used):
                        result['applications'].append(copy.deepcopy(a)); used.update(labels)
        if revision < 3 and isinstance(result['actions'], list) and all(isinstance(a, dict) for a in result['actions']):
            known = {a.get('id') for a in result['actions']}
            result['actions'].extend(copy.deepcopy(a) for a in DEFAULT['actions']
                                     if a['id'] == 'song_process' and a['id'] not in known)
    return result


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Настройки команд должны быть объектом')
    c = config(value)
    if not isinstance(c['actions'], list) or not isinstance(c['applications'], list) or len(c['applications']) > 300:
        raise ValueError('Неверный список команд или приложений (не более 300 приложений)')
    seen, templates = set(), {}
    for a in c['actions']:
        if not isinstance(a, dict) or a.get('id') not in IDS or a['id'] in seen:
            raise ValueError('Неизвестное или повторяющееся действие')
        seen.add(a['id'])
        if not isinstance(a.get('title'), str) or not 1 <= len(a['title']) <= 120:raise ValueError('Укажите название действия')
        if not isinstance(a.get('enabled'), bool):raise ValueError('Укажите, включено ли действие')
        phrases = a.get('phrases')
        if not isinstance(phrases, list) or not 1 <= len(phrases) <= 100:
            raise ValueError('Для действия нужно от 1 до 100 фраз')
        slot = '{запрос}' if a['id'] in QUERY_ACTIONS else '{приложение}'
        for phrase in phrases:
            if a['id'] in LITERAL_ACTIONS:
                if not isinstance(phrase, str) or not 3 <= len(phrase) <= 160 or re.search(r'[{}\n\r]', phrase):
                    raise ValueError('Для фокусировки укажите цельную фразу без подстановок')
                key = norm(phrase)
                if key in templates:raise ValueError('Повторяющаяся фраза: ' + phrase)
                templates[key] = a['id']
                continue
            if not isinstance(phrase, str) or not 3 <= len(phrase) <= 160 or phrase.count(slot) != 1:
                raise ValueError('Каждая фраза должна содержать ровно один ' + slot)
            before, after = phrase.split(slot)
            if not before.strip() or re.search(r'[{}\n\r]', before + after):
                raise ValueError('Перед подстановкой нужно название команды; фигурные скобки — только для ' + slot)
            key = norm(phrase).replace(slot, '{цель}')
            if key in templates:raise ValueError('Повторяющаяся фраза: ' + phrase)
            templates[key] = a['id']
    if seen != IDS:raise ValueError('Нужно сохранить все встроенные действия; ненужное можно выключить')
    names = {}
    for app in c['applications']:
        if not isinstance(app, dict):raise ValueError('Неверная запись приложения')
        for key in ('name', 'target'):
            if not isinstance(app.get(key), str) or not app[key].strip() or len(app[key]) > 200:
                raise ValueError('Укажите название и системное имя приложения')
        aliases = app.get('aliases')
        if not isinstance(aliases, list) or len(aliases) > 100:raise ValueError('Не более 100 названий у приложения')
        for alias in [app['name'], app['target']] + aliases:
            if not isinstance(alias, str) or not norm(alias) or len(alias) > 200:raise ValueError('Пустое или слишком длинное название приложения')
            key = norm(alias)
            if key in names and names[key] != app['target']:
                raise ValueError('Название «' + alias + '» назначено разным приложениям')
            names[key] = app['target']
    return c


def target(name, c):
    key = norm(name)
    matches = {a['target'] for a in c['applications'] if key in {norm(v) for v in [a['name'], a['target']] + a['aliases']}}
    if len(matches) > 1:raise ValueError('Уточните приложение: ' + name)
    return next(iter(matches), name)


def parse(text, value=None):
    from media_intent import protected
    if protected(text):
        return None
    c = config(value)
    t = ' '.join(str(text).strip().split()).rstrip('.!')
    t = re.sub(r'^(?:пожалуйста[, ]+|(?:можешь|можно)\s+)', '', t, flags=re.I)
    matches = []
    for a in c['actions']:
        if a['id'] in LITERAL_ACTIONS:
            literal = re.sub(r'[, ]+пожалуйста$', '', t, flags=re.I)
            if any(norm(literal) == norm(phrase) for phrase in a['phrases']):
                matches.append((1000, {'action': a['id'], 'enabled': a['enabled'], 'title': a['title']}))
            continue
        slot = '{запрос}' if a['id'] in QUERY_ACTIONS else '{приложение}'
        for phrase in a['phrases']:
            before, after = phrase.split(slot)
            pattern = re.escape(before.strip()) + r'\s+(.+?)' + (r'\s+' + re.escape(after.strip()) if after.strip() else '')
            candidate = re.sub(r'[, ]+пожалуйста$', '', t, flags=re.I) if slot == '{приложение}' else t
            m = re.fullmatch(pattern, candidate, re.I)
            if not m:continue
            name = m[1].strip(' «»"„“')
            if slot == '{приложение}':
                if re.search(r'\b(?:сайт|страниц|вкладк|задач|файл|папк|ссылк)\w*\b', t, re.I):continue
                name = re.sub(r'^(?:приложени[еяюи]|программ[ауы]|программе)\s+', '', name, flags=re.I).strip(' «»"„“')
                if re.fullmatch(r'(?:(?:на|в)\s+)?(?:приложени[еяюи]|программ[ауы]|программе|все(?:\s+приложения|\s+программы)?)', name, re.I):continue
            if name:
                request = {'action': a['id'], 'enabled': a['enabled'], 'title': a['title']}
                request['query' if slot == '{запрос}' else 'application'] = name if slot == '{запрос}' else target(name, c)
                matches.append((len(before) + len(after), request))
    if not matches:return None
    matches.sort(key=lambda x: x[0], reverse=True)
    best = [r for score, r in matches if score == matches[0][0]]
    if len({r['action'] for r in best}) > 1:raise ValueError('Команда соответствует нескольким действиям. Уточните фразы в настройках.')
    return best[0]
