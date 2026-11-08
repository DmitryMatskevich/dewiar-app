"""Isolated notification transport entry point."""
import json
import sys
from pathlib import Path
from .access import send

if __name__ == '__main__':
    p = json.load(sys.stdin)
    try:
        import egress
        from settings import Settings
        egress.install(Settings())
        egress.set_local_only(bool(Settings().get('privacy', 'local_only')))
        result = {'state':send(p['channel'], p['body'], Path(p['config']), p['settings'], p['message_id'])}
    except ValueError as e:
        result = {'state':'failed','error':str(e)[:180]}
    except Exception:
        # SMTP and HTTP may have accepted the message before disconnecting.
        result = {'state':'uncertain','error':'Транспорт не подтвердил отправку; повтор вручную'}
    print(json.dumps(result))
