"""Author CRUD/provenance contract; only temporary SQLite, no network/settings."""
import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from content.store import ContentStore, SCHEMA_VERSION
from content import authors, video_store


class AuthorsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = ContentStore(self.tmp.name)
        self.data = {'name': 'Тестовый автор', 'bio': 'Тестовая биография', 'site_names': {'site-dcnt-ru': 'Подпись DCNT', 'site-landmaster-ru': 'Подпись LAND'}}

    def create(self, key='new'): return authors.create(self.store, self.data, key)

    def test_create_retry_noop_conflict_and_immutable_history(self):
        first = self.create()
        self.assertEqual(self.create(), first)
        self.assertEqual(authors.update(self.store, first['id'], 1, {}), first)
        updated = authors.update(self.store, first['id'], 1, {'name': 'Другое имя'})
        self.assertEqual(updated['revision'], 2)
        self.assertEqual(self.create(), first)
        with self.assertRaisesRegex(ValueError, 'revision_conflict'): authors.update(self.store, first['id'], 1, {'name': 'Потеря'})
        with self.assertRaisesRegex(ValueError, 'request_conflict'): authors.create(self.store, {'name': 'Другое'}, 'new')
        with self.store.db() as c:
            self.assertEqual(json.loads(c.execute('SELECT snapshot FROM content_author_revisions WHERE revision=1').fetchone()[0]), first)

    def test_two_concurrent_edits_only_one_wins(self):
        first = self.create()
        def change(value):
            try: return authors.update(self.store, first['id'], 1, {'bio': value})['bio']
            except ValueError as e: return str(e).split(':')[0]
        with concurrent.futures.ThreadPoolExecutor(2) as pool: results = list(pool.map(change, ['Первый', 'Второй']))
        self.assertEqual(results.count('revision_conflict'), 1)

    def test_archive_restore_and_stale_byline(self):
        first = self.create()
        self.assertEqual(authors.byline(self.store, first['id'], 1, 'site-dcnt-ru')['byline'], 'Подпись DCNT')
        archived = authors.update(self.store, first['id'], 1, {}, True)
        with self.assertRaisesRegex(ValueError, 'revision_conflict'): authors.byline(self.store, first['id'], 1)
        with self.assertRaisesRegex(ValueError, 'архив'): authors.byline(self.store, first['id'], 2)
        with self.assertRaisesRegex(ValueError, 'архив'): authors.update(self.store, first['id'], 2, {'bio': 'lost'})
        self.assertEqual(authors.listing(self.store)['total'], 0)
        self.assertEqual(authors.listing(self.store, archived=True)['total'], 1)
        restored = authors.update(self.store, first['id'], archived['revision'], {}, False)
        self.assertEqual(authors.byline(self.store, first['id'], restored['revision'])['byline'], self.data['name'])

    def test_copied_byline_does_not_follow_profile_changes(self):
        author = self.create()
        name = authors.byline(self.store, author['id'], 1, 'site-dcnt-ru')['byline']
        article = self.store.create({'title':'Тестовая статья','author':name}, 'article')
        video = video_store.create(self.store, {'data':{'title':'Тестовое видео','author':name}, 'request_key':'video'})
        authors.update(self.store, author['id'], 1, {'site_names': {'site-dcnt-ru': 'Новая подпись'}})
        self.assertEqual(self.store.get(article['id'])['author'], name)
        self.assertEqual(video_store.get(self.store, video['id'])['author'], name)

    def test_five_distinct_projects_search_and_summary(self):
        first = self.create(); self.create('other-same-name')
        result = authors.listing(self.store, query='ТЕСТОВЫЙ', limit=1)
        self.assertEqual(result['total'], 2); self.assertEqual(len(result['items']), 1)
        self.assertNotIn('bio', result['items'][0]); self.assertNotIn('site_names', result['items'][0])
        self.assertEqual(authors.listing(self.store, query='%')['total'], 0)
        self.assertEqual(authors.byline(self.store, first['id'], 1, 'site-landmaster-ru')['byline'], 'Подпись LAND')
        self.assertEqual(authors.byline(self.store, first['id'], 1, 'site-lanmaster-ru')['byline'], self.data['name'])

    def test_validation_and_wire_fields(self):
        for data in [{'name':' '}, {'name':'a' * 241}, {'name':'a','bio':'x' * 4001}, {'name':'a','site_names':{'missing':'x'}}, {'name':'a','site_names':{'site-dcnt-ru':''}}, {'name':'a','token':'secret'}]:
            with self.assertRaises(ValueError): authors.create(self.store, data, 'bad')
        first = self.create()
        for args in [{'id':first['id'],'expected_revision':1}, {'id':first['id'],'expected_revision':1,'archived':1}]:
            with self.assertRaises(ValueError): authors.dispatch(self.store,'content_author_archive',args)
        with self.assertRaises(ValueError): authors.byline(self.store, first['id'], 1, ['site-dcnt-ru'])
        with self.assertRaises(ValueError): authors.listing(self.store, limit=True)

    def test_v13_migration_preserves_existing_author_strings(self):
        article = self.store.create({'title':'Старое','author':'Старый автор'}, 'legacy')
        with self.store.db() as c:
            before = c.execute('SELECT snapshot FROM revisions').fetchall()[0][0]
            c.execute('DROP TABLE content_video_revision_sources')
            c.execute('DROP TABLE content_oauth_events');c.execute('DROP TABLE content_oauth_connections')
            c.execute('DROP TABLE content_revision_sources'); c.execute('DROP TABLE content_source_records')
            c.execute('DROP TABLE content_video_review_revisions'); c.execute('DROP TABLE content_video_reviews'); c.execute('DROP TABLE content_caption_revisions'); c.execute('DROP TABLE content_captions'); c.execute('DROP TABLE content_manual_retractions'); c.execute('DROP TABLE content_author_revisions'); c.execute('DROP TABLE content_authors'); c.execute('PRAGMA user_version=13')
        migrated = ContentStore(self.tmp.name)
        self.assertEqual(migrated.get(article['id'])['author'], 'Старый автор')
        with migrated.db() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(c.execute('SELECT snapshot FROM revisions').fetchone()[0], before)
        self.assertEqual(authors.listing(migrated)['total'], 0)


if __name__ == '__main__': unittest.main()
