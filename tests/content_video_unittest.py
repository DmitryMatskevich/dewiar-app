"""Offline video metadata/storyboard tests; no Engine, media or provider imports."""
import concurrent.futures
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from content.store import ContentStore, SCHEMA_VERSION
from content import video_store as video
from content.commands import dispatch


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ContentStore(self.temp.name)

    def make(self, **data):
        return video.create(self.store, {'data': {'title': 'ТЕСТ видео', **data}, 'request_key': 'new'})

    def scene(self, ident='scene1', **kw):
        return {'id': ident, 'title': 'Кадр', 'narration': 'Текст речи', 'visual_prompt': 'Стойка', 'duration_us': 1250001, 'locked': False, **kw}

    def test_crud_reopen_summary_and_noop(self):
        row = self.make(scenes=[self.scene()])
        saved = video.update(self.store, {'id': row['id'], 'expected_revision': 1, 'patch': {'title': 'Русский сценарий'}})
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(video.update(self.store, {'id': row['id'], 'expected_revision': 2, 'patch': {}}), saved)
        listing = video.listing(ContentStore(self.temp.name), {'query': 'РУССКИЙ'})
        self.assertEqual(listing['total'], 1)
        self.assertEqual(listing['items'][0]['duration_us'], 1250001)
        self.assertNotIn('scenes', listing['items'][0])
        self.assertNotIn('script', listing['items'][0])

    def test_request_retry_after_article_changed(self):
        article = self.store.create({'title': 'Исходник', 'body': 'Факт 12'}, 'article')
        args = {'request_key': 'derived', 'source': {'kind': 'article', 'id': article['id'], 'revision': 1}}
        row = video.create(self.store, args)
        self.store.update(article['id'], 1, {'body': 'Факт 30'})
        self.assertEqual(video.create(self.store, args), row)
        source = video.source(self.store, {'id': row['id']})
        self.assertTrue(source['changed'])
        self.assertEqual(source['text'], 'Факт 12')
        self.assertEqual(row['script'], 'Факт 12')
        with self.assertRaisesRegex(ValueError, 'request_conflict'):
            video.create(self.store, {**args, 'data': {'title': 'Другой'}})

    def test_long_source_saved_without_silent_truncation(self):
        article = self.store.create({'title': 'Длинный', 'body': 'a' * 100001}, 'article')
        row = video.create(self.store, {'request_key': 'derived', 'source': {'kind': 'article', 'id': article['id'], 'revision': 1}})
        self.assertEqual(row['script'], '')
        self.assertTrue(row['warnings'])
        source = video.source(self.store, {'id': row['id'], 'offset': 100000})
        self.assertEqual(source['text'], 'a')
        self.assertEqual(source['total'], 100001)

    def test_derived_reel_is_independent_selected_scenes_in_original_order(self):
        parent = self.make(scenes=[self.scene('a'), self.scene('b', narration='Вторая')])
        args = {'request_key': 'reel', 'source': {'kind': 'video', 'id': parent['id'], 'revision': 1, 'scene_ids': ['b']}}
        reel = video.create(self.store, args)
        self.assertNotEqual(reel['id'], parent['id'])
        self.assertEqual((reel['kind'], reel['aspect'], reel['script']), ('reel', '9:16', 'Вторая'))
        video.update(self.store, {'id': parent['id'], 'expected_revision': 1, 'patch': {'scenes': []}})
        self.assertEqual(video.get(self.store, reel['id'])['scenes'], [parent['scenes'][1]])
        self.assertTrue(video.source(self.store, {'id': reel['id']})['changed'])
        with self.assertRaises(ValueError):
            video.create(self.store, {**args, 'request_key': 'bad', 'source': {**args['source'], 'scene_ids': ['absent']}})

    def test_concurrent_edit_only_one_wins(self):
        row = self.make()
        def edit(title):
            try:
                return video.update(self.store, {'id': row['id'], 'expected_revision': 1, 'patch': {'title': title}})['revision']
            except ValueError as e:
                return str(e).split(':')[0]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(edit, ['Один', 'Два']))
        self.assertCountEqual(results, [2, 'revision_conflict'])

    def test_archive_restore_and_history_immutable(self):
        row = self.make(scenes=[self.scene()])
        archived = video.update(self.store, {'id': row['id'], 'expected_revision': 1, 'archived': True}, archive=True)
        self.assertTrue(archived['archived'])
        with self.assertRaisesRegex(ValueError, 'восстановите'):
            video.update(self.store, {'id': row['id'], 'expected_revision': 2, 'patch': {'script': 'Скрытая правка'}})
        video.update(self.store, {'id': row['id'], 'expected_revision': 2, 'archived': False}, archive=True)
        video.update(self.store, {'id': row['id'], 'expected_revision': 3, 'patch': {'scenes': []}})
        restored = video.update(self.store, {'id': row['id'], 'expected_revision': 4, 'revision': 1}, restore=True)
        self.assertEqual(restored['revision'], 5)
        self.assertEqual(restored['scenes'], row['scenes'])
        self.assertEqual(video.get(self.store, row['id'], 1), row)
        self.assertNotIn('script', video.revisions(self.store, {'id': row['id']})[0])

    def test_validation_rollback(self):
        for bad in [{'title': ''}, {'kind': 'film'}, {'aspect': 'wrong'}, {'site_id': 'site-other'},
                    {'scenes': [self.scene(), self.scene()]}, {'scenes': [self.scene(duration_us=1.2)]},
                    {'scenes': [self.scene(locked='yes')]}, {'script': 'x' * 100001},
                    {'scenes': [self.scene(duration_us=True)]}, {'scenes': [self.scene(duration_us=0)]},
                    {'scenes': [self.scene(**{'unsafe': 'field'})]}]:
            with self.subTest(bad=str(bad)[:100]), self.assertRaises(ValueError):
                self.make(**bad)
        self.assertEqual(video.listing(self.store, {})['total'], 0)
        self.assertEqual(self.make()['revision'], 1)

    def test_scene_reorder_delete_and_original_revision(self):
        row = self.make(scenes=[self.scene('a'), self.scene('b'), self.scene('c')])
        saved = video.update(self.store, {'id': row['id'], 'expected_revision': 1, 'patch': {'scenes': [row['scenes'][2], row['scenes'][0]]}})
        self.assertEqual([s['id'] for s in saved['scenes']], ['c', 'a'])
        self.assertEqual(len(video.get(self.store, row['id'], 1)['scenes']), 3)

    def test_kind_cannot_change_and_invalid_source(self):
        row = self.make()
        with self.assertRaises(ValueError):
            video.update(self.store, {'id': row['id'], 'expected_revision': 1, 'patch': {'kind': 'reel'}})
        for source in [[], {'kind': 'article', 'id': 'absent', 'revision': 1}, {'kind': 'video', 'id': row['id'], 'revision': 0}]:
            with self.assertRaises(ValueError):
                video.create(self.store, {'request_key': 'derived', 'source': source})

    def test_dispatch_pagination_and_sql_like_is_literal(self):
        for i in range(3):
            dispatch(self.store, 'content_video_create', {'request_key': str(i), 'data': {'title': f'Карточка {i}'}})
        self.assertEqual(len(dispatch(self.store, 'content_video_list', {'offset': 1, 'limit': 1})['items']), 1)
        self.assertEqual(video.listing(self.store, {'query': '%'})['total'], 0)
        with self.assertRaises(ValueError):
            dispatch(self.store, 'content_video_get', [])

    def test_migration_from_schema10_preserves_article_bytes(self):
        article = self.store.create({'title': 'Старый материал', 'body': 'Оригинал'}, 'article')
        with self.store.db() as c:
            c.execute('DROP TABLE content_oauth_events');c.execute('DROP TABLE content_oauth_connections')
            c.execute('DROP TABLE content_revision_sources'); c.execute('DROP TABLE content_source_records')
            before = c.execute('SELECT snapshot FROM revisions WHERE material_id=?', (article['id'],)).fetchone()[0]
            for table in ('content_video_review_revisions', 'content_video_reviews', 'content_caption_revisions', 'content_captions', 'content_manual_retractions', 'content_author_revisions', 'content_authors', 'content_provider_diagnostics', 'content_video_imports', 'content_video_handoffs', 'content_video_sources', 'content_video_revisions', 'content_videos'):
                c.execute('DROP TABLE ' + table)
            c.execute('DROP TABLE content_video_revision_sources')
            c.execute('PRAGMA user_version=10')
        reopened = ContentStore(self.temp.name)
        self.assertEqual(reopened.get(article['id']), article)
        with reopened.db() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(c.execute('SELECT snapshot FROM revisions WHERE material_id=?', (article['id'],)).fetchone()[0], before)
        self.assertEqual(video.listing(reopened, {})['items'], [])


if __name__ == '__main__':
    unittest.main()
