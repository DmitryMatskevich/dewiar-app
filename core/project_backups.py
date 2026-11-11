"""Project profiles and verified ZIP creation for the backup task workflow."""

import re
import time
import hashlib
import json
import os
import zipfile
from pathlib import Path
from datetime import datetime


PROJECT_BACKUPS = {
    "cmms-rus": {
        "title": "CMMS-rus",
        "aliases": ("cmms-rus", "cmms", "dc master", "dcmaster", "дсмастер", "дс мастер"),
        "root": Path.home() / "Documents/cmms-rus",
        "script": Path("_utilities/backup_cmms_rus.py"),
        "python": "python3",
        "args": (),
        "scope_note": "Архив содержит исходники по правилам сценария; database, media и другие исключённые им данные не копируются.",
    },
}

# Projects in cmms-rus/_utilities/zip_dcmaster.py, excluding cmms-rus itself.
# Names and exclusions mirror that script; unavailable source directories are
# deliberately omitted until they exist on this Mac.
_ZIP_DCMASTER_PROJECTS = [
    ("cmms-draw", "cmms_draw", "Редактор JavaScript"),
    ("dcmaster-ai", "dcmaster_ai", "Cистему управления ЦОД DCMASTER"),
    ("china-search", "china_search", "China Search — поиск китайских поставщиков Демо/Разраб"),
    ("site-cmms", "site_cmms", "Сайт DC MASTER dcmms.ru"),
    ("cmms-vk", "cmms_vk", "Сборка Вконтактке CMMS"),
    ("dc-digest", "dc-digest", "DC Digest по рынку ЦОД"),
    ("site-dcnt", "site_dcnt", "Сайт dcnt"),
    ("site-ockc", "site_ockc", "Сайт ockc"),
    ("3d-lanmaster", "3d_lanmaster", "3d товары для Lanmaster"),
]
_ZIP_IGNORED_DIRS = ("pycache", "migrations", "venv", ".git", "database", "design", "media", "node_modules")
_ZIP_ALWAYS_INCLUDE = {
    ".gitignore", ".dockerignore", ".env.example", ".env.customer.example",
    ".env.production.example", ".markdownlint.json", ".eslintrc", ".eslintignore",
    ".prettierrc", ".prettierignore", ".sassrc", ".cursorrules", ".editorconfig",
    ".nvmrc", ".python-version",
}
_ZIP_PROJECT_IGNORED_DIRS = {
    "china_search": (".next", ".next-build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
                     "tessdata", "tmp", "outputs", "report", "logs", "dist", "build", "coverage"),
}
_ZIP_PROJECT_IGNORED_EXTENSIONS = {
    "china_search": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico", "tsbuildinfo"},
}
_ZIP_PROJECT_IGNORED_FILES = {
    "china_search": {".env", ".env.local", "update_cmms_log.txt"},
}

for _project_id, _dirname, _title in _ZIP_DCMASTER_PROJECTS:
    _root = Path.home() / "Documents" / _dirname
    if _root.is_dir():
        PROJECT_BACKUPS[_project_id] = {
            "title": _title,
            "aliases": (_dirname, _title, _project_id),
            "root": _root,
            "mode": "zip_dcmaster",
            "source_dir": _dirname,
            "scope_note": "Фильтры взяты из zip_dcmaster.py; .git, node_modules, venv, database, media и другие указанные исключения не копируются.",
        }

DEFAULT_OUTPUT = Path.home() / 'Documents/_Save_Project/SaveDevAll'
CLOUD_DEFAULTS = {'yandex': 'SaveDevAll', 'google': '1lsKtVJBoy6Cs1fQ7ffDIYSd-fWBKd4EX'}
RELEASE_DIR = 'astro-prototype/dist'
FIXED_EXCLUDED_DIRS = {'.git', '.ssh', '.gnupg', '.codegraph', 'private-data',
                       'ockc-form-data', 'ockc-comments-data', 'dcnt-form-data', 'dcnt-comments-data'}
PROVIDERS = {'local': 'Локальная папка', 'yandex': 'Яндекс.Диск', 'google': 'Google Диск'}

for key, dirname, title, aliases in (
    ('site-dcnt', 'site_dcnt', 'Сайт DCNT', ('dcnt', 'site_dcnt', 'сайт dcnt', 'сайта dcnt', 'дцнт',
                                           'дснт', 'сайт дцнт', 'сайта дцнт', 'сайт дснт', 'dcnt.ru', 'цоды')),
    ('site-ockc', 'site_ockc', 'Сайт ОСКС', ('ockc', 'оскс', 'сайт оскс', 'сайта оскс', 'сайт ockc', 'ockc.ru',
                                             'об скс', 'о скс')),
):
    PROJECT_BACKUPS[key] = {
        'title': title, 'root': Path.home() / 'Documents' / dirname, 'aliases': aliases,
        'mode': 'dist_archive', 'archive_subdir': RELEASE_DIR, 'output_dir': DEFAULT_OUTPUT,
        'cloud_destinations': CLOUD_DEFAULTS, 'source_dir': dirname + '_dist',
        'scope_note': 'Готовая сборка Astro с документами и медиа.',
    }


_SOURCE_CACHE = ('node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache',
                 '.ruff_cache', '.next', '.next-build', '.cache', '.pnpm-store', 'coverage',
                 '.playwright-mcp', '.cursor', '.claude', '.codex')
_SOVERIAZ_REVIEW_IMAGES = (
    'buttons-dcmms-style-check desktop-icons-hover-check desktop-premium-check desktop-top-premium-check '
    'featured-animated-check featured-dynamic-check featured-final-desktop featured-final-mobile '
    'featured-redesign-check featured-redesign-mobile-check flicker-before-hover flicker-before-top '
    'header-soft-check image-backed-dark-check infra-services-check light-icons-active-check '
    'light-icons-mobile-check light-sweep-hover-check location-map-check location-mobile-check '
    'location-mobile-final-check mobile-check mobile-icons-check mobile-icons-final-check '
    'mobile-icons-fixed-check mobile-premium-check mobile-top-check no-flicker-top-check '
    'planner-mobile-final-check planner-mobile-final-v2 planner-mobile-mode-check planner-mobile-web-check '
    'planner-room-final-check planner-room-final-v2 planner-room-mode-check planner-room-web-check '
    'planner-site-final-check planner-site-final-v2 planner-site-mode-check theme-dark-featured-check '
    'theme-dark-top-check theme-light-featured-check theme-light-top-check'
).split()
_SOURCE_PROJECTS = {
    'site-cmms': (
        'site_cmms', 'Сайт DC MASTER', ('сайт cmms', 'сайта cmms', 'сайт dc master', 'dcmms.ru'),
        ('.dockerignore', '.gitignore', 'Dockerfile', 'README.md', 'api', 'assessment_app', 'assets',
         'deploy_files.py', 'document', 'favicon-120.png', 'favicon.ico', 'favicon.svg', 'index.html',
         'llms.txt', 'modules', 'nginx.conf', 'privacy', 'robots.txt', 'scripts', 'search', 'services',
         'sitemap.xml', 'startdocker.sh', 'thank-you', 'tmp_deploy_site_cmms.sh', 'tmp_site_cmms_nginx.conf',
         'vozmozhnosti', 'worker', 'yandex_9858be7113defcdd.html'),
        ('api/data', 'assessment_app/data'),
        ('Dockerfile', 'index.html', 'services/index.html', 'api/main.py',
         'assessment_app/alembic.ini', 'assessment_app/migrations/env.py', 'worker/src/index.js'),
        'README.md и assessment_app/README.md: восстановить сайт и SaaS из исходников, применить миграции. '
        'Настройки и состояние рабочих баз восстанавливаются отдельно.',
    ),
    'site-soveriaz': (
        'site_soveriaz', 'Сайт Soveriaz', ('сайт соверайз', 'соверайз', 'soveriaz', 'soveraiz', 'sklad-podolsk.ru', 'склад подольск'),
        ('.dockerignore', '.github', '.gitignore', '3d-warehouse-planner', 'AGENTS.md', 'README.md',
         'STATUS.md', 'api', 'apple-touch-icon.png', 'ask-question', 'assets', 'audio-recorder.js',
         'complex-layout.js', 'complex3d.js', 'deploy', 'engineering-networks-reference.js',
         'favicon-32.png', 'favicon.svg', 'gate-maneuvers.js', 'index.html', 'legal-address-podolsk',
         'llms-full.txt', 'llms.txt', 'localized-contact-form.js', 'mezzanine-layout.js', 'mezzanine3d.js',
         'model-preview-zh.js', 'model-preview.css', 'news', 'office-rent-podolsk', 'package.json',
         'photo-plans', 'plan/demo-room.json', 'plan/demo-territory.json', 'plan/complex-model.md',
         'plan/mezzanine-model.md', 'plan/warehouse-2-model.md', 'plan/engineering-source-audit-2026-10-09.json',
         'planner3d.js', 'pnpm-lock.yaml', 'privacy', 'question-form.js', 'rack-layout.js', 'robots.txt',
         'script.js', 'site-cta.js', 'site-header.js', 'site.webmanifest', 'sitemap.xml', 'smartcaptcha.js',
         'styles.css', 'tests', 'transport-access-podolsk', 'vendor', 'warehouse-access-reference.js',
         'warehouse-complex-podolsk', 'warehouse-gate-reference.js', 'warehouse-heating-podolsk',
         'working-aisles.js', 'yandex_0fc2896bb4206e60.html', 'zh-cn'),
        ('assets/prepared-photos', 'api/data', *('assets/' + name + '.png' for name in _SOVERIAZ_REVIEW_IMAGES)),
        ('index.html', '3d-warehouse-planner/index.html', 'planner3d.js', 'complex3d.js', 'mezzanine3d.js',
         'plan/demo-room.json', 'plan/demo-territory.json', 'vendor/three.module.min.js', 'pnpm-lock.yaml'),
        'Развернуть статические страницы с assets, vendor и plan/*.json; для тестов установить зависимости '
        'из pnpm-lock.yaml. Конфигурация API и рабочая SQLite-база восстанавливаются отдельно.',
    ),
    'site-xok': (
        'site_xok', 'Сайт XOK', ('сайт хок', 'хок', 'сайт xok', 'xok', 'xok.ru', 'клюх', 'хоккей'),
        ('.',), (), ('index.html', 'assets/styles.css', 'assets/site.js', 'deploy/nginx-xok-http.conf'),
        'Содержимое архива — статический сайт. Разместить в корне nginx по инструкции deploy/README.md.',
    ),
    'china-search': (
        'china_search', 'China Search', ('китай поиск', 'поиск китайских поставщиков', 'chinalook', 'китай', 'чайна'),
        ('.env.production.example', '.gitattributes', '.gitignore', 'Makefile', 'README.md', 'backend',
         'deploy', 'docker-compose.dev.yml', 'docker-compose.prod.yml', 'docs', 'documents/admin',
         'documents/https-migration-runbook.md', 'frontend', 'logistic/README.md', 'logistic/companies.json',
         'logistic/schema.json', 'restart.sh', 'scripts', 'start.sh', 'update_cmms.command'),
        ('backend/data', 'frontend/out'),
        ('backend/uv.lock', 'backend/alembic.ini', 'backend/migrations/env.py', 'backend/src/main.py',
         'backend/src/domain/logistics/data/companies_seed.json', 'frontend/package-lock.json',
         'docker-compose.prod.yml', 'logistic/companies.json'),
        'Воссоздать .env по образцам, загрузить eng и chi_sim в backend/data/tessdata перед Docker-сборкой, '
        'установить зависимости по lock-файлам и применить миграции Alembic. PostgreSQL и рабочие данные отдельно.',
    ),
    'site-lantester': (
        'site_lantester', 'Сайт LANTESTER',
        ('lantester', 'lantester.ru', 'сайт lantester', 'сайт лантестер', 'лантестер'),
        ('.gitattributes', '.gitignore', 'AGENTS.md', 'README.md', 'components.json', 'design.md',
         'tokens.css', 'package.json', 'assets', 'dist', 'scripts', 'src', 'static-test',
         'deploy/llms.txt'),
        ('output', 'outputs', 'tmp', 'backups', 'wordpress-prod', 'plan', '.hallmark', '.codex-local'),
        ('dist/index.html', 'dist/robots.txt', 'dist/sitemap.xml', 'dist/assets/local-copy.js',
         'dist/assets/local-copy.css', 'static-test/scripts/mirror-prod.mjs',
         'static-test/scripts/check-links.mjs', 'package.json'),
        'Восстановить статический сайт из dist и локального слоя static-test; '
        'старая WordPress-копия, каталоги результатов, локальные backup и временные файлы не входят.',
    ),
}

for key, (dirname, title, aliases, include, exclude, required, note) in _SOURCE_PROJECTS.items():
    PROJECT_BACKUPS[key] = {
        'title': title, 'root': Path.home() / 'Documents' / dirname, 'aliases': (dirname, *aliases),
        'mode': 'source_archive', 'source_dir': dirname + '_source',
        'include': include, 'exclude': exclude, 'required': required, 'scope_note': note,
        'cloud_destinations': CLOUD_DEFAULTS,
    }


def normalize(value):
    return re.sub(r'[^\w]+', ' ', str(value or '').casefold(), flags=re.UNICODE).strip()


def _defaults(project_id):
    spec = PROJECT_BACKUPS[project_id]
    mode = spec.get('mode', 'cmms')
    excludes = []
    if mode != 'dist_archive':
        excludes = ['**/' + name for name in _ZIP_IGNORED_DIRS]
        excludes += ['**/' + name for name in _ZIP_PROJECT_IGNORED_DIRS.get(spec.get('source_dir'), ())]
    if mode == 'cmms':
        excludes = ['**/' + name for name in ('.git', '.venv', 'venv', 'node_modules', '__pycache__', 'pycache',
                    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox', 'coverage', 'database', 'media',
                    'artifacts', 'Отчеты', 'logs')]
        excludes += ['dcmaster/static', 'cmms-rus/netbox', '.codex_tmp/models', '.codex_tmp/profiler', 'cursor_report']
    if mode == 'source_archive':
        excludes = ['**/' + name for name in _SOURCE_CACHE] + list(spec['exclude'])
    return {
        'title': spec['title'], 'root': str(spec['root']), 'aliases': list(spec['aliases']),
        'include': list(spec.get('include', (spec.get('archive_subdir', '.'),))), 'exclude': excludes,
        'output_dir': str(spec.get('output_dir', DEFAULT_OUTPUT.parent / 'cmms-rus' if mode == 'cmms' else DEFAULT_OUTPUT)),
        'destinations': [{'provider': key, 'target': value} for key, value in spec.get('cloud_destinations', {}).items()],
    }


def _items(value, label):
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, str) for x in value):
        raise ValueError(f'{label}: укажите список из не более чем 100 строк.')
    return list(dict.fromkeys(x.strip() for x in value if x.strip()))


def _relative(value, anywhere=False):
    if value == '.' and not anywhere:
        return value
    base = value.removeprefix('**/') if anywhere else value
    if (not base or any(x in ('', '.', '..') for x in base.split('/'))
            or any(c in base for c in '\\:*?[]\x00\n\r')
            or (base != value and '/' in base)):
        raise ValueError(f'Укажите путь относительно проекта: {value}')
    return value


def _absolute(value, label):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in '\x00\n\r'):
        raise ValueError(f'{label}: укажите полный путь к папке.')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError(f'{label}: укажите полный путь к папке.')
    return str(path.resolve())


def _target(provider, value):
    from urllib.parse import unquote, urlsplit

    if not isinstance(value, str) or not value.strip():
        raise ValueError('Укажите папку назначения.')
    value = value.strip()
    if provider == 'local':
        return _absolute(value, 'Папка назначения')
    if provider == 'google':
        if value.startswith('https://'):
            url = urlsplit(value)
            match = re.fullmatch(r'/drive/(?:u/\d+/)?folders/([\w-]+)/?', url.path, flags=re.ASCII)
            value = match[1] if url.netloc == 'drive.google.com' and match else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{10,120}', value):
            raise ValueError('Укажите ссылку на папку Google Диска или её ID.')
        return value
    if provider == 'yandex':
        if value.startswith('https://'):
            url = urlsplit(value)
            if url.netloc != 'disk.yandex.ru' or not url.path.startswith('/client/disk/'):
                raise ValueError('Укажите путь внутри Яндекс.Диска или ссылку на личную папку.')
            value = unquote(url.path.removeprefix('/client/disk/'))
        value = value.strip('/')
        return _relative(value) if value and value != '.' else '/'
    raise ValueError('Такой тип назначения не поддерживается.')


def validate_profile(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('title'), str) or not raw['title'].strip():
        raise ValueError('Укажите название проекта.')
    title = raw['title'].strip()
    if len(title) > 100 or '\n' in title or '\r' in title:
        raise ValueError('Название проекта должно помещаться в одну строку до 100 символов.')
    root = _absolute(raw.get('root'), 'Папка проекта')
    output = _absolute(raw.get('output_dir'), 'Папка архивов')
    if output == root:
        raise ValueError('Укажите отдельную папку для архивов.')
    include = [_relative(x) for x in _items(raw.get('include'), 'Включить')]
    if not include:
        raise ValueError('Укажите хотя бы одну папку или файл. Точка означает весь проект.')
    exclude = [_relative(x, anywhere=True) for x in _items(raw.get('exclude'), 'Исключить')]
    aliases = _items(raw.get('aliases', []), 'Названия для ассистента')
    destinations = raw.get('destinations', [])
    if not isinstance(destinations, list) or len(destinations) > 20:
        raise ValueError('Укажите до 20 назначений для копий.')
    rows = []
    for row in destinations:
        if not isinstance(row, dict) or row.get('provider') not in PROVIDERS:
            raise ValueError('Выберите тип назначения.')
        dest = {'provider': row['provider'], 'target': _target(row['provider'], row.get('target'))}
        if dest['provider'] == 'local' and dest['target'] == root:
            raise ValueError('Копия архива должна храниться в отдельной папке.')
        if dest not in rows:
            rows.append(dest)
    return dict(title=title, root=root, output_dir=output, include=include, exclude=exclude,
                aliases=aliases, destinations=rows)


def configured_projects(settings=None):
    overrides = settings.get('project_backups', 'profiles') or {} if settings is not None else {}
    profiles = {key: _defaults(key) for key in PROJECT_BACKUPS}
    profiles.update(overrides)
    return profiles


def backup_settings(settings):
    profiles = configured_projects(settings)
    return {'projects': [dict(profile, id=key, preset=key in PROJECT_BACKUPS) for key, profile in profiles.items()],
            'providers': PROVIDERS}


def save_profile(settings, project_id, profile):
    import uuid

    current = configured_projects(settings)
    if project_id and project_id not in current:
        raise ValueError('Проект не найден.')
    key = project_id or 'project-' + uuid.uuid4().hex[:12]
    overrides = settings.get('project_backups', 'profiles') or {}
    if profile is None:
        if key not in PROJECT_BACKUPS:
            raise ValueError('У нового проекта нет настроек по умолчанию.')
        overrides.pop(key, None)
    else:
        profile = validate_profile(profile)
        names = {normalize(x) for x in [profile['title'], *profile['aliases']]}
        for other, value in current.items():
            if other != key and names.intersection(normalize(x) for x in [other, value['title'], *value['aliases']]):
                raise ValueError(f'Название или псевдоним уже используется проектом «{value["title"]}».')
        overrides[key] = profile
    settings.update({'project_backups': {'profiles': overrides}})
    return dict(backup_settings(settings), selected=key)


def resolve_project(value, profiles=None):
    profiles = configured_projects() if profiles is None else profiles
    name = normalize(value)
    # Voice input commonly adds a grammatical wrapper around the configured
    # name: «сайт DCNT», «сайта DCNT», «проект CMMS».  Keep aliases editable
    # in settings, but accept these wrappers without duplicating every case.
    candidates = {name}
    for prefix in ('сайт ', 'сайта ', 'проект ', 'проекта ', 'страница ', 'страницы '):
        if name.startswith(prefix):
            candidates.add(name[len(prefix):].strip())
    for key, profile in profiles.items():
        known = {normalize(x) for x in [key, profile['title'], *profile['aliases']]}
        if candidates.intersection(known):
            return key
    raise ValueError('Не узнал проект. Выберите его в разделе «Задачи → Проекты → Резервирование».')


def resolve_projects(values, profiles=None):
    if isinstance(values, str):
        values = [values]
    result = list(dict.fromkeys(resolve_project(value, profiles) for value in values or ()))
    if not result:
        raise ValueError('Выберите хотя бы один проект.')
    return result


def recognize_backup(text, profiles):
    """Handle explicit local commands, leaving other conversation to the assistant."""
    value = normalize(text)
    match = re.fullmatch(
        r'(?:пожалуйста )?((?:сделай|создай|запусти|выполни|сохрани|сохранить) '
        r'(?:бэкап|бекап|копию|резерв(?:ирование|ную копию)?)'
        r'|забэкапь|забекапь|зарезервируй|бэкапировать|бекапировать|резервировать|бэкап|бекап|резервирование|резерв)'
        r' (?:для )?(.+?)(?: пожалуйста)?', value)
    if not match:
        return None
    if text.rstrip().endswith('?') and match[1] in {'бэкап', 'бекап', 'резерв', 'резервирование'}:
        return None
    project = match[2]
    if project in ('всех проектов', 'все проекты'):
        return list(profiles)
    try:
        return [resolve_project(project, profiles)]
    except ValueError:
        names = re.split(r'\s+и\s+', project)
        if len(names) > 1:
            try:
                return resolve_projects(names, profiles)
            except ValueError:
                pass
        return None


def _skip(relative, profile, protected, ignored_extensions, ignored_files):
    parts = relative.parts
    if FIXED_EXCLUDED_DIRS.intersection(parts) or parts[-1] in ignored_files:
        return True
    name = parts[-1]
    if (name in {'.DS_Store', '.env', 'vault.bin', 'vault.key', 'wp-config.php', 'id_rsa', 'id_ed25519'}
            or name.startswith('.env.') and not name.endswith(('.example', '.sample', '.template'))
            or relative.suffix.lower() in {'.pem', '.key', '.p12', '.pfx'}):
        return True
    if relative.suffix.lower().lstrip('.') in ignored_extensions and name not in _ZIP_ALWAYS_INCLUDE:
        return True
    value = relative.as_posix()
    if any(relative == p or p in relative.parents for p in protected):
        return True
    return any(pattern[3:] in parts if pattern.startswith('**/') else
               value == pattern or value.startswith(pattern + '/') for pattern in profile['exclude'])


def _inventory(root, profile, preset):
    def raise_walk_error(error):
        raise error

    protected = set()
    locations = [profile['output_dir'], *(row['target'] for row in profile['destinations'] if row['provider'] == 'local')]
    for location in locations:
        path = Path(location).resolve()
        if path.is_relative_to(root):
            protected.add(path.relative_to(root))
    source_dir = preset.get('source_dir', '')
    ignored_extensions = _ZIP_PROJECT_IGNORED_EXTENSIONS.get(source_dir, set())
    ignored_files = _ZIP_PROJECT_IGNORED_FILES.get(source_dir, set())
    if preset.get('script'):
        ignored_extensions = {'pyc', 'pyo', 'tmp', 'swp', 'log'}
        ignored_files = {'update_cmms_log.txt'}
    if preset.get('mode') == 'source_archive':
        ignored_extensions = {'pyc', 'pyo', 'swp', 'log', 'db', 'sqlite', 'sqlite3', 'sqlite-wal',
                              'sqlite-shm', 'db-wal', 'db-shm', 'sqlite3-wal', 'sqlite3-shm', 'tsbuildinfo'}
    files, excluded = {}, 0
    for include in profile['include']:
        path = root / include
        if path.resolve() != path or not path.exists():
            raise ValueError(f'Путь не найден или является символической ссылкой: {include}')
        walk = (os.walk(path, followlinks=False, onerror=raise_walk_error)
                if path.is_dir() else [(path.parent, [], [path.name])])
        for current, dirs, names in walk:
            base = Path(current)
            kept = []
            for name in sorted(dirs):
                candidate = base / name
                if candidate.is_symlink() or _skip(candidate.relative_to(root), profile, protected, set(), set()):
                    excluded += 1
                else:
                    kept.append(name)
            dirs[:] = kept
            for name in sorted(names):
                candidate = base / name
                relative = candidate.relative_to(root)
                if (candidate.is_symlink() or not candidate.is_file()
                        or _skip(relative, profile, protected, ignored_extensions, ignored_files)):
                    excluded += 1
                    continue
                member = relative
                if preset.get('archive_subdir') and relative.is_relative_to(preset['archive_subdir']):
                    member = relative.relative_to(preset['archive_subdir'])
                if preset.get('script'):
                    member = Path('cmms-rus') / member
                entry = member.as_posix()
                if entry in files and files[entry] != candidate:
                    raise ValueError(f'Два файла имеют одинаковый путь внутри ZIP: {entry}')
                files[entry] = candidate
    if not files:
        raise ValueError('По выбранным правилам в архив не попадает ни один файл.')
    if preset.get('archive_subdir') and profile['include'] == [preset['archive_subdir']] and not profile['exclude']:
        if not {'index.html', 'sitemap.xml'} <= files.keys():
            raise ValueError('В готовой сборке нет главной страницы или карты сайта.')
    if preset.get('required') and profile['include'] == list(preset['include']):
        missing = set(preset['required']).difference(files)
        if missing:
            raise ValueError('В архиве исходников отсутствуют обязательные файлы: ' + ', '.join(sorted(missing)))
    return files, excluded


def run_project_backup(project_id, *, profile=None, cancel=None, on_progress=None):
    if not re.fullmatch(r'[a-z0-9-]+', project_id):
        raise ValueError('Неверный идентификатор проекта.')
    preset = PROJECT_BACKUPS.get(project_id, {})
    if profile is None:
        profile = _defaults(project_id)
    profile = validate_profile(profile)
    root = Path(profile['root']).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f'Папка проекта не найдена: {root}')
    files, excluded = _inventory(root, profile, preset)
    directory = Path(profile['output_dir'])
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')
    name = preset.get('source_dir') or project_id
    archive = directory / f'{name}_{stamp}.zip'
    partial, manifest = archive.with_suffix('.zip.partial'), archive.with_suffix('.manifest.json')
    started = time.monotonic()

    def check():
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Создание резервной копии остановлено пользователем.')

    try:
        compression = (zipfile.ZIP_DEFLATED
                       if preset.get('script') or preset.get('mode') == 'source_archive' else zipfile.ZIP_STORED)
        with zipfile.ZipFile(partial, 'x', compression=compression, allowZip64=True) as zf:
            for index, (member, path) in enumerate(files.items(), 1):
                check()
                before = path.stat()
                zf.write(path, arcname=member)
                after = path.stat()
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise RuntimeError(f'Файл изменился во время архивации: {member}')
                if on_progress and (index % 100 == 0 or index == len(files)):
                    on_progress(index, len(files), round(time.monotonic() - started))
        with zipfile.ZipFile(partial) as zf:
            if zf.namelist() != list(files) or zf.testzip() is not None:
                raise RuntimeError('ZIP не прошёл проверку содержимого.')
        digest = hashlib.sha256()
        with partial.open('rb') as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                check()
                digest.update(block)
        size, sha = partial.stat().st_size, digest.hexdigest()
        manifest.write_text(json.dumps({'project': profile['title'], 'source': str(root), 'archive': str(archive),
            'files': len(files), 'excluded': excluded, 'bytes': size, 'sha256': sha, 'verified': True,
            'include': profile['include'], 'exclude': profile['exclude'], 'entries': list(files),
            'restore': preset.get('scope_note', '')},
            ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(partial, archive)
    except BaseException:
        partial.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        raise
    return {'returncode': 0, 'output': (f'Included files: {len(files)}\nExcluded paths: {excluded}\n'
        f'Created: {archive} ({size} bytes)\nSHA-256: {sha}\nManifest: {manifest}\n'
        'Verified: ZIP CRC, entries, size, and SHA-256\n')}


def describe_result(project_id, output, profile=None):
    def line(pattern):
        match = re.search(pattern, output, re.MULTILINE)
        return match.group(1).strip() if match else ''

    return {'project': (profile or _defaults(project_id))['title'],
            'included': line(r'^Included files:[ \t]*(.+)$'),
            'excluded': line(r'^Excluded paths:[ \t]*(.+)$'),
            'archive': line(r'^Created: (.+) \([^()]*\)$'),
            'size': line(r'^Created:.*\(([^()]*)\)$'),
            'sha256': line(r'^SHA-256: (.+)$'), 'manifest': line(r'^Manifest: (.+)$'),
            'scope_note': PROJECT_BACKUPS.get(project_id, {}).get('scope_note', ''),
            'verified': line(r'^Verified: (.+)$')}
