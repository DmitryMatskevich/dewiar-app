"""Read voice settings and prepare narrowly scoped changes for explicit confirmation."""
import hashlib
import json
import secrets
import time

import voice_commands as vc


def fingerprint(value):
    return hashlib.sha256(json.dumps(vc.config(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def pending(engine, proposal_id):
    proposal = getattr(engine, '_voice_command_proposals', {}).get(str(proposal_id))
    if not proposal or time.monotonic() - proposal['created'] > 600:
        raise ValueError('Предложение не найдено или устарело. Подготовьте изменение заново.')
    if fingerprint(engine.s.get('voice_commands')) != proposal['before']:
        raise ValueError('Настройки уже изменились. Прочитайте их и подготовьте новое предложение.')
    return proposal


def propose(engine, args, installed):
    current = engine.s.get('voice_commands')
    draft = vc.config(current)
    kind = args.get('change')
    if kind in ('add_phrase', 'remove_phrase', 'set_enabled'):
        action = next((x for x in draft['actions'] if x['id'] == args.get('action')), None)
        if not action:raise ValueError('Укажите существующее действие из настроек голосовых команд.')
        if kind == 'set_enabled':
            enabled = args.get('enabled')
            if not isinstance(enabled, bool):raise ValueError('Укажите enabled: true или false.')
            action['enabled'] = enabled
            summary = ('Включить' if enabled else 'Выключить') + ' голосовую команду «' + action['title'] + '»'
        else:
            phrase = str(args.get('phrase') or '').strip()
            if not phrase:raise ValueError('Укажите точную фразу; для приложений нужна {приложение}, для песни — {запрос}, для фокусировки — цельная фраза без подстановок.')
            if kind == 'add_phrase':
                if any(vc.norm(x) == vc.norm(phrase) for x in action['phrases']):raise ValueError('Эта фраза уже есть в настройках.')
                action['phrases'].append(phrase)
            else:
                if not any(vc.norm(x) == vc.norm(phrase) for x in action['phrases']):raise ValueError('Такой фразы нет в настройках.')
                action['phrases'] = [x for x in action['phrases'] if vc.norm(x) != vc.norm(phrase)]
            summary = ('Добавить' if kind == 'add_phrase' else 'Удалить') + ' фразу «' + phrase + '» для действия «' + action['title'] + '»'
    elif kind in ('add_alias', 'remove_alias'):
        query = str(args.get('application') or '').strip()
        alias = str(args.get('alias') or '').strip()
        if not query or not alias:raise ValueError('Укажите приложение и точный вариант его названия.')
        canonical = vc.target(query, draft)
        app = next((x for x in draft['applications'] if vc.norm(x['target']) == vc.norm(canonical)), None)
        if app is None:
            from mac_control import resolve
            row = resolve(query, installed, draft)
            app = {'name': row['name'], 'target': row['id'], 'aliases': []}
            draft['applications'].append(app)
        exists = any(vc.norm(x) == vc.norm(alias) for x in app['aliases'])
        if kind == 'add_alias':
            if exists:raise ValueError('Этот вариант названия уже сохранён.')
            app['aliases'].append(alias)
        else:
            if not exists:raise ValueError('Такого дополнительного названия нет. Основное имя приложения не удаляется.')
            app['aliases'] = [x for x in app['aliases'] if vc.norm(x) != vc.norm(alias)]
        summary = ('Добавить' if kind == 'add_alias' else 'Удалить') + ' название «' + alias + '» для приложения «' + app['name'] + '»'
    else:raise ValueError('Неизвестный вид изменения голосовых команд.')
    vc.validate(draft)
    if fingerprint(draft) == fingerprint(current):raise ValueError('Такая настройка уже установлена.')
    proposal_id = secrets.token_hex(8)
    engine._voice_command_proposals = {proposal_id: {'before': fingerprint(current), 'config': draft,
                                                   'summary': summary, 'created': time.monotonic()}}
    return {'proposal_id': proposal_id, 'summary': summary, 'applied': False,
            'next': 'Предложение подготовлено, настройки не изменены. Для применения вызови voice_command_apply с proposal_id: система покажет это изменение и спросит разрешение пользователя.'}


def apply(engine, proposal_id):
    # The caller uses Toolbox.guard + the normal user confirmation flow.
    # Hold the settings lock to prevent an editor save racing with this proposal.
    with engine.s._lock:
        proposal = pending(engine, proposal_id)
        engine.s.update({'voice_commands': proposal['config']})
        engine._voice_command_proposals = {}
    if hasattr(engine, 'emit'):engine.emit('settings', settings=engine.s.snapshot())
    return {'applied': True, 'summary': proposal['summary'], 'message': 'Сохранено. Голосовые команды обновлены без перезапуска.'}
