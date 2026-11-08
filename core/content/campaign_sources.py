"""Transactional campaign-source migration, preserving old package identities."""
import os
import sqlite3
import uuid
from contextlib import closing


def backup_before_migration(store, version, target_version=18):
    # Caller holds BEGIN IMMEDIATE, so no writer can change the source during
    # this separate read-only SQLite backup (including WAL pages).
    folder = store.root / 'backups'
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / f'before-v{version}-to-v{target_version}-{uuid.uuid4().hex}.db'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(store.path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(path)) as target:
                source.backup(target, pages=256)
                if target.execute('PRAGMA user_version').fetchone()[0] != version:
                    raise ValueError('Версия резервной копии изменилась')
                if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Резервная копия контента не прошла проверку')
    except Exception:
        path.unlink(missing_ok=True)
        raise


def migrate(c):
    # SQLite documented create/copy/drop/rename order. Never rename the old
    # parent first: that would retarget child FKs to the temporary name.
    dependent = c.execute("SELECT sql FROM sqlite_master WHERE tbl_name='content_campaigns' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
    columns = {r['name'] for r in c.execute('PRAGMA table_info(content_campaigns)')}
    c.execute('''CREATE TABLE content_campaigns_new(
        id TEXT PRIMARY KEY, material_id TEXT REFERENCES materials(id),
        source_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
        archived INTEGER NOT NULL, snapshot TEXT NOT NULL,
        video_id TEXT REFERENCES content_videos(id),
        CHECK((material_id IS NOT NULL) != (video_id IS NOT NULL)))''')
    video = 'video_id' if 'video_id' in columns else 'NULL'
    c.execute('INSERT INTO content_campaigns_new(rowid,id,material_id,source_revision,revision,archived,snapshot,video_id) '
              'SELECT rowid,id,material_id,source_revision,revision,archived,snapshot,' + video + ' FROM content_campaigns')
    c.execute('DROP TABLE content_campaigns')
    c.execute('ALTER TABLE content_campaigns_new RENAME TO content_campaigns')
    for row in dependent:
        c.execute(row[0])
