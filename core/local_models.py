"""One read-only inventory for local chat models; activation is always explicit."""
import json
import os
import subprocess
from pathlib import Path
import httpx


def lmstudio_models():
    from llm import lmstudio_cli
    cli = lmstudio_cli()
    if not cli:
        return []
    result = subprocess.run([str(cli), 'ls', '--json'], capture_output=True, text=True,
                            timeout=15, stdin=subprocess.DEVNULL,
                            creationflags=0x08000000 if os.name == 'nt' else 0)
    if result.returncode:
        raise ValueError('LM Studio не вернула локальный каталог')
    models = []
    for item in json.loads(result.stdout):
        if item.get('type') != 'llm':
            continue
        variants = item.get('variants') or [item.get('modelKey') or item.get('path')]
        for variant in variants:
            if variant:
                models.append({'model': variant, 'name': item.get('displayName') or variant,
                               'publisher': item.get('publisher'), 'parameters': item.get('paramsString'),
                               'architecture': item.get('architecture'), 'format': item.get('format'),
                               'quantization': variant.split('@', 1)[1] if '@' in variant else (item.get('quantization') or {}).get('name'),
                               'context': item.get('maxContextLength'), 'vision': item.get('vision'),
                               'tools': item.get('trainedForToolUse'), 'catalog_id': item.get('modelKey'),
                               'variant': variant.split('@', 1)[-1] if '@' in variant else '',
                               'size_gib': round(item.get('sizeBytes', 0) / 1024**3, 1)})
    return models


def ollama_downloads():
    root = Path(os.environ.get('OLLAMA_MODELS') or Path.home() / '.ollama/models')
    entries = []
    manifests = root / 'manifests'
    for path in sorted(manifests.glob('*/*/*/*')):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
            layers = data.get('layers', [])
            if not all((root / 'blobs' / layer['digest'].replace(':', '-')).is_file() for layer in layers):
                continue
            registry, namespace, name, tag = path.relative_to(manifests).parts
            model = ('' if namespace == 'library' else namespace + '/') + name + ':' + tag
            if registry != 'registry.ollama.ai':model = registry + '/' + model
            entries.append({'model': model, 'name': model,
                            'size_gib': round(sum(x.get('size', 0) for x in layers) / 1024**3, 1)})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return entries


def inventory(strata):
    from llm import local_installations
    apps = local_installations()
    rows, errors = [], []
    for app in apps:
        entries = [{'model': m, 'name': m} for m in app['models']]
        if app['preset'] == 'ollama' and not entries:entries = ollama_downloads()
        try:
            if app['preset'] == 'lmstudio':
                local = lmstudio_models()
                known = {m['model'].split('@')[0] for m in local}
                entries = local + [m for m in entries if m['model'].split('@')[0] not in known and 'embed' not in m['model'].lower()]
            else:
                response = httpx.get(app['base_url'].removesuffix('/v1') + '/api/tags', timeout=2)
                response.raise_for_status()
                entries = [{'model': m['name'], 'name': m['name'],
                            'size_gib': round(m.get('size', 0) / 1024**3, 1),
                            'parameters': m.get('details', {}).get('parameter_size'),
                            'format': m.get('details', {}).get('format'),
                            'architecture': m.get('details', {}).get('family'),
                            'quantization': m.get('details', {}).get('quantization_level')}
                           for m in response.json().get('models', [])]
        except (OSError, ValueError, subprocess.SubprocessError, httpx.HTTPError) as e:
            if app['installed'] and not entries:
                errors.append(app['name'] + ': каталог недоступен; откройте приложение и обновите список.')
        for entry in entries:
            rows.append(dict(entry, app=app['name'], source=app['preset'],
                             base_url=app['base_url'], state='cloud' if entry['model'].endswith(':cloud') else 'available' if app['running'] else 'downloaded'))
    status = strata.status()
    for item in status['catalog']:
        owned = status['state'] == 'running' and strata.package_id == item['id']
        rows.append({'name': item['name'], 'model': item['id'], 'source': 'strata', 'app': 'Strata',
                     'state': 'available' if owned else 'downloaded' if item['ready'] else 'incomplete',
                     'size_gib': item.get('disk_gib'), 'ram_gib': item.get('ram_required_gib'),
                     'owned': owned, 'runtime': status['runtime']})
    if status.get('desktop_api'):
        from strata_local import probe
        try:
            profile = probe(status['desktop_api'])
            for model in profile['models']:
                rows.append({'name': model, 'model': model, 'source': 'strata_api', 'app': 'Strata',
                             'base_url': profile['base_url'], 'state': 'available'})
        except ValueError:
            errors.append('Strata: API недоступен; проверьте ключ в дополнительных настройках.')
    return {'models': rows, 'errors': errors}


def connect(strata, source, model):
    """Resolve a fresh inventory row, never accept an executable or URL from the UI."""
    from llm import LLM_PRESETS, lmstudio_start, _models_at
    from strata_local import MemoryShortage, probe
    row = next((r for r in inventory(strata)['models'] if r['source'] == source and r['model'] == model), None)
    if not row:
        raise ValueError('Модель больше не найдена. Обновите список.')
    if source == 'strata':
        if row['owned']:
            return strata.connection()
        try:
            strata.start(model, 4096)
        except MemoryShortage as error:
            return {'ok': False, 'memory': error.payload()}
        return {'loading': True}
    if source == 'strata_api':
        return dict(probe(row['base_url']), model=model)
    base = row['base_url']
    if source == 'lmstudio':
        base, _ = lmstudio_start()
    if not base or _models_at(base, 3) is None:
        raise ValueError('Откройте ' + row['app'] + ' и включите локальный сервер, затем повторите подключение.')
    return dict(LLM_PRESETS[source], base_url=base, model=model)


def details(strata, source, model):
    """Read metadata without loading weights; report catalog limits separately from runtime."""
    from urllib.parse import quote
    row = next((r for r in inventory(strata)['models'] if r['source'] == source and r['model'] == model), None)
    if not row:
        raise ValueError('Модель больше не найдена. Обновите список.')
    result = dict(row, metadata_source=row['app'] + ' · локальный каталог')
    try:
        if source == 'ollama':
            response = httpx.post(row['base_url'].removesuffix('/v1') + '/api/show', json={'model': model}, timeout=5)
            response.raise_for_status()
            data = response.json(); info = data.get('model_info') or {}; caps = data.get('capabilities', [])
            architecture = info.get('general.architecture') or row.get('architecture')
            result.update(context=info.get(str(architecture) + '.context_length'),
                          description=info.get('general.description'), license=data.get('license'),
                          vision='vision' in caps, tools='tools' in caps, reasoning='thinking' in caps,
                          metadata_source='Ollama · /api/show (без загрузки модели)')
            name = model.split(':')[0]
            if '/' not in name:
                result['catalog_url'] = 'https://ollama.com/library/' + quote(name, safe='')
        elif source == 'lmstudio':
            key = row.get('catalog_id') or model.split('@')[0]
            if len(key.split('/')) == 2:
                result['catalog_url'] = 'https://lmstudio.ai/models/' + quote(key, safe='/')
            response = httpx.get(row['base_url'].removesuffix('/v1') + '/api/v1/models', timeout=3)
            response.raise_for_status()
            item = next((x for x in response.json().get('models', []) if x.get('key') == key), None)
            if item:
                caps = item.get('capabilities') or {}
                reasoning = caps.get('reasoning')
                instances = item.get('loaded_instances') or []
                result.update(description=item.get('description'),
                              reasoning=('on' in reasoning.get('allowed_options', [])) if isinstance(reasoning, dict) else None,
                              loaded_contexts=[i.get('config', {}).get('context_length') for i in instances
                                               if i.get('config', {}).get('context_length')],
                              metadata_source='LM Studio · локальный каталог и /api/v1/models')
    except (httpx.HTTPError, ValueError, TypeError):
        result['metadata_note'] = 'Сервер не вернул дополнительные сведения. Показаны данные локального каталога.'
    return result


def open_app(source):
    """Launch/foreground a known model app. Never load model weights here."""
    import sys
    names = {'lmstudio': 'LM Studio', 'ollama': 'Ollama',
             'strata': 'Strata', 'strata_api': 'Strata'}
    name = names.get(source)
    if not name:
        raise ValueError('Неизвестное приложение моделей')
    if sys.platform == 'darwin':
        try:
            result = subprocess.run(['/usr/bin/open', '-a', name], capture_output=True,
                                    text=True, timeout=15, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError('Не удалось открыть ' + name + '. Попробуйте открыть приложение вручную.') from error
        if result.returncode:
            raise ValueError('Не удалось открыть ' + name + '. Проверьте, что приложение установлено.')
    else:
        from tools import t_open_app
        t_open_app({'name': name}, {})
    return {'app': name, 'message': 'Открыто приложение ' + name}
