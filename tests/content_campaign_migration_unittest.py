"""Schema 17 fixtures, WAL-safe backup and FK rollback; temp data only."""
import json
import sqlite3
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import content_distribution_unittest as fixtures
from content import campaign_sources,distribution,manual,packages,queue,video_store
from content.store import ContentStore,SCHEMA_VERSION


class MigrationTests(unittest.TestCase):
    setUp=fixtures.DistributionTests.setUp
    add=fixtures.DistributionTests.add
    campaign=fixtures.DistributionTests.campaign

    def legacy(self):
        self.camp=self.campaign(request_key='migration-campaign')
        self.variant=self.camp['variants'][0]
        self.package=packages.prepare(self.store,{'variant_id':self.variant['id'],'expected_revision':1})
        self.receipt=manual.report(self.store,{'package_hash':self.package['hash'],'permalink':'https://linkedin.com/feed/update/TEST'})
        scope=queue.record_scope_from_trusted_ui(self.store,[self.package['hash']],'TEST',200,clock=lambda:100)
        queue.enqueue(self.store,self.package['hash'],scope,'1970-01-01T00:01:40+00:00',clock=lambda:100)
        # Construct exact legacy parent schema, leaving every child table intact.
        with self.store.db() as c:
            c.execute('PRAGMA foreign_keys=OFF');c.execute('BEGIN IMMEDIATE')
            c.execute('DROP TABLE content_oauth_events');c.execute('DROP TABLE content_oauth_connections')
            c.execute('DROP TABLE content_revision_sources');c.execute('DROP TABLE content_source_records')
            c.execute('CREATE TABLE old_campaigns(id TEXT PRIMARY KEY,material_id TEXT NOT NULL REFERENCES materials(id),source_revision INTEGER NOT NULL,revision INTEGER NOT NULL,archived INTEGER NOT NULL,snapshot TEXT NOT NULL)')
            c.execute('INSERT INTO old_campaigns SELECT id,material_id,source_revision,revision,archived,snapshot FROM content_campaigns')
            c.execute('DROP TABLE content_campaigns');c.execute('ALTER TABLE old_campaigns RENAME TO content_campaigns')
            c.execute('CREATE INDEX test_campaign_index ON content_campaigns(material_id)')
            c.execute('DROP TABLE content_video_revision_sources')
            c.execute('PRAGMA user_version=17')
        return self.snapshots()

    def snapshots(self):
        with self.store.db() as c:
            return {name:[tuple(r) for r in c.execute('SELECT * FROM '+name)] for name in
                    ['content_variants','content_variant_revisions','content_packages','content_manual_receipts','content_outbox','request_keys']}

    def test_migration_preserves_children_hashes_retry_and_backup(self):
        before=self.legacy();store=ContentStore(self.tmp.name)
        self.assertEqual(self.snapshots(),before)
        self.assertEqual(packages.prepare(store,{'variant_id':self.variant['id'],'expected_revision':1}),self.package)
        self.assertEqual(self.campaign(request_key='migration-campaign')['id'],self.camp['id'])
        with store.db() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
            self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0],1)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(c.execute('PRAGMA foreign_key_list(content_variants)').fetchone()['table'],'content_campaigns')
            self.assertIsNotNone(c.execute("SELECT name FROM sqlite_master WHERE name='test_campaign_index'").fetchone())
        backups=list((store.root/'backups').glob('*.db'));self.assertEqual(len(backups),1)
        with sqlite3.connect(backups[0]) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],17)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('SELECT snapshot FROM content_packages').fetchone()[0],json.dumps(self.package['payload'],ensure_ascii=False,sort_keys=True,separators=(',',':')))
        restored_root=store.root/'restore-test';restored_root.mkdir()
        shutil.copy2(backups[0],restored_root/'content.db')
        restored=ContentStore(restored_root)
        self.assertEqual(distribution.get(restored,self.camp['id']),distribution.get(store,self.camp['id']))
        self.assertEqual(packages.prepare(restored,{'variant_id':self.variant['id'],'expected_revision':1}),self.package)
        ContentStore(self.tmp.name);self.assertEqual(len(list((store.root/'backups').glob('*.db'))),1)

    def test_exactly_one_source_and_foreign_keys(self):
        self.legacy();store=ContentStore(self.tmp.name)
        video=video_store.create(store,{'data':{'title':'TEST'},'request_key':'video'})
        with store.db() as c:
            c.execute('INSERT INTO content_campaigns VALUES (?,?,?,?,?,?,?)',('video',None,1,1,0,'{}',video['id']))
            for material,vid in [(None,None),(self.article['id'],video['id']),(None,'missing')]:
                with self.assertRaises(sqlite3.IntegrityError):c.execute('INSERT INTO content_campaigns VALUES (?,?,?,?,?,?,?)',('invalid',material,1,1,0,'{}',vid))

    def test_failure_rolls_back_schema_rows_and_version(self):
        before=self.legacy();migrate=campaign_sources.migrate
        def fail(c):migrate(c);raise RuntimeError('TEST failure after rebuild')
        with patch.object(campaign_sources,'migrate',fail),self.assertRaises(RuntimeError):ContentStore(self.tmp.name)
        self.assertEqual(self.snapshots(),before)
        with self.store.db() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],17)
            self.assertNotIn('video_id',{r['name'] for r in c.execute('PRAGMA table_info(content_campaigns)')})
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        ContentStore(self.tmp.name)

    def test_invalid_child_aborts_before_commit(self):
        self.legacy()
        with self.store.db() as c:
            c.execute('PRAGMA foreign_keys=OFF');c.execute("UPDATE content_variants SET campaign_id='missing'")
        with self.assertRaisesRegex(ValueError,'нарушает связи'):ContentStore(self.tmp.name)
        with self.store.db() as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],17)


if __name__=='__main__':unittest.main()
