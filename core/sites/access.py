"""Adapters to existing Dewiar secrets. No secrets in site records or list APIs."""
import json
from pathlib import Path


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def catalog(config):
    errors = []
    vault = []
    try:
        from vault import Vault
        vault = [{k: x.get(k, '') for k in ('id', 'name', 'login', 'url')} for x in Vault(config / 'vault.bin', config / 'vault.key').list()]
    except Exception:
        errors.append('Сейф недоступен этому процессу. Используйте сейф в Dewiar.')
    servers = [{k: x.get(k, '') for k in ('id', 'name', 'host', 'user', 'port')} for x in read_json(config / 'servers.json', [])]
    rules = read_json(config / 'mail-rules.json', {})
    accounts = [{k: x.get(k, '') for k in ('id', 'address', 'label')} for x in rules.get('accounts', [])]
    channels = {}
    for channel in ('telegram', 'max'):
        d = read_json(config / (channel + '.json'), {})
        channels[channel] = bool((d.get('owner') or {}).get('chat'))
    return dict(vault=vault, servers=servers, accounts=accounts, channels=channels, errors=errors)


def reveal(config, item_id):
    from vault import Vault
    item = Vault(config / 'vault.bin', config / 'vault.key').get(item_id)
    if not item:
        raise ValueError('Запись сейфа не найдена')
    return {'value': item['value']}


def send(channel, body, config, settings, message_id):
    if channel == 'simulation':
        return 'simulated'
    if channel in ('telegram', 'max'):
        if channel == 'telegram':
            from telegram import Telegram
            bot = Telegram(path=config / 'telegram.json')
        else:
            from maxbot import Max
            bot = Max(path=config / 'max.json')
        if not bot.paired():
            raise ValueError('Мессенджер не подключён к владельцу')
        if not bot.send(body):
            raise RuntimeError('Отправка не подтверждена мессенджером')
        return 'sent'
    if channel == 'email':
        from mail import Mail
        account_id = settings['mail_account_id']
        allowed = {a['id'] for a in catalog(config)['accounts']}
        if account_id not in allowed:
            raise ValueError('Выбранный ящик не найден')
        Mail(config / 'mail-accounts' / (account_id + '.json')).send(settings['email_to'], '[Dewiar] Мониторинг сайтов', body, message_id=message_id)
        return 'sent'
    raise ValueError('Неизвестный канал')
