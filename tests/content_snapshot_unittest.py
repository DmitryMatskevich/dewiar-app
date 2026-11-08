"""Isolated SQLite/WAL backup and restore tests; no Engine or remote work."""
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from backup import Backup,BackupError
from content.store import ContentStore
from content import snapshots as sn,assets,oauth_store,queue as q
from content.db_lock import gate
from content.mcp_helper import HelperError
import content_queue_unittest as queue_fixture
from content_oauth_store_unittest import result


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.cfg=self.root/'config';self.state=self.root/'state';self.ws=self.root/'workspace';self.copies=self.root/'copies'
        self.cfg.mkdir();self.state.mkdir();self.ws.mkdir();(self.cfg/'settings.json').write_text('{"test":"original"}')
        self.store=ContentStore(self.ws/'Контент');self.backup=Backup(self.cfg,self.state,self.ws)
        self.material=self.store.create({'title':'TEST original','body':'original'},'original')
    def make(self):return self.backup.make(self.copies,fmt='zip')['path']
    def edit(self):return self.store.update(self.material['id'],1,{'title':'TEST changed','body':'changed'})
    def rewrite(self,path,change):
        with zipfile.ZipFile(path) as z:entries={n:z.read(n) for n in z.namelist()}
        change(entries);target=self.copies/'modified.zip'
        with zipfile.ZipFile(target,'w') as z:
            for k,v in entries.items():z.writestr(k,v)
        return target
    def mutate_db(self,entries,sql):
        path=self.root/'mutate.db';path.write_bytes(entries[sn.DATABASE])
        with closing(sqlite3.connect(path)) as c:c.executescript(sql);c.commit()
        entries[sn.DATABASE]=path.read_bytes();entries.pop(sn.MANIFEST,None);path.unlink()
    def test_uncheckpointed_wal_and_same_inode_restore_with_open_connection(self):
        with closing(sqlite3.connect(self.store.path)) as anchor:
            anchor.execute('PRAGMA wal_autocheckpoint=0')
            anchor.execute('SELECT count(*) FROM materials').fetchone()
            sha=assets.put(self.store,b'TEST original asset','text/plain')
            self.edit();self.assertTrue(Path(str(self.store.path)+'-wal').stat().st_size)
            saved=self.make();inode=self.store.path.stat().st_ino
            with zipfile.ZipFile(saved) as z:
                self.assertFalse(any(n.endswith(('-wal','-shm','.lock')) for n in z.namelist()))
                manifest=json.loads(z.read(sn.MANIFEST));self.assertEqual(hashlib.sha256(z.read(sn.DATABASE)).hexdigest(),manifest['database_sha256'])
            self.store.update(self.material['id'],2,{'title':'TEST later'})
            (self.store.root/'assets'/sha).unlink()
            restored=self.backup.restore(saved)
            self.assertEqual(self.store.path.stat().st_ino,inode)
            self.assertEqual(anchor.execute('SELECT title FROM materials').fetchone()[0],'TEST changed')
            self.assertEqual(self.store.get(self.material['id'])['revision'],2)
            self.assertEqual(assets.read(self.store,sha),b'TEST original asset')
            self.assertTrue(Path(restored['before']).is_file());self.assertEqual(restored['content']['omitted_media'],0)
    def test_merge_keeps_current_database_and_restores_separate_workspace(self):
        saved=self.make();self.edit();before=self.store.get(self.material['id'])
        restored=self.backup.restore(saved,mode='merge');new=restored['content']
        self.assertEqual(self.store.get(self.material['id']),before)
        other=ContentStore(Path(new['workspace'])/'Контент');self.assertEqual(other.get(self.material['id'])['title'],'TEST original')
        self.assertEqual(new['mode'],'separate_workspace');self.assertIn(new['workspace'],Path(new['report_path']).read_text())
        self.assertFalse(other.root.is_relative_to(self.ws))
    def test_corrupt_future_fk_bad_media_and_alias_rejected_before_settings(self):
        sha=assets.put(self.store,b'TEST media','text/plain');saved=self.make();(self.cfg/'settings.json').write_text('{"test":"current"}');self.edit()
        cases=[lambda e:e.__setitem__(sn.DATABASE,b'corrupt'),
               lambda e:self.mutate_db(e,'PRAGMA user_version=999;'),
               lambda e:self.mutate_db(e,"UPDATE revisions SET material_id='missing';"),
               lambda e:e.__setitem__('workspace/Контент/assets/'+sha,b'wrong same'),
               lambda e:e.__setitem__('workspace/Контент/content.db-wal',b'old wal'),
               lambda e:e.__setitem__('workspace/./Контент/content.db',e.pop(sn.DATABASE))]
        for change in cases:
            with self.subTest(change=change):
                bad=self.rewrite(saved,change)
                with self.assertRaises(BackupError):self.backup.restore(bad)
                self.assertEqual((self.cfg/'settings.json').read_text(),'{"test":"current"}')
                self.assertEqual(self.store.get(self.material['id'])['title'],'TEST changed')
    def test_queue_and_oauth_are_paused_only_in_restored_copy(self):
        fx=queue_fixture.QueueTests();fx.setUp();self.addCleanup(fx.doCleanups)
        # Use the real queue fixture, then copy it into this temporary workspace.
        with sn.snapshot(fx.store.path) as (copy,_):sn.restore_database(copy,self.store.path)
        fx.store=self.store;row,job,_=fx.start()
        auth=oauth_store.create(self.store,'TEST OAuth','oauth');intent=oauth_store.begin(self.store,auth['id'],1,'authorize','login')
        saved=self.make()
        self.assertEqual(q.get(self.store,row['id'])['state'],'publishing')
        with self.assertRaisesRegex(BackupError,'завершения текущей отправки'):self.backup.restore(saved)
        with self.store.db() as c:self.assertEqual(c.execute('SELECT revoked FROM content_scopes').fetchone()[0],0)
        restored=self.backup.restore(saved,mode='merge');other=ContentStore(Path(restored['content']['workspace'])/'Контент')
        value=q.get(other,row['id']);self.assertEqual(value['state'],'publication_unknown');self.assertIsNone(value['owner']);self.assertGreater(value['fence'],job['fence'])
        self.assertIsNone(q.claim(other,'restored-worker',clock=fx.clock));self.assertFalse(q.finish_send(other,job,{'outcome':'accepted','remote_id':'old'},clock=fx.clock))
        with other.db() as c:self.assertEqual(c.execute('SELECT revoked FROM content_scopes').fetchone()[0],1)
        self.assertEqual(oauth_store.get(other,auth['id'])['state'],'needs_login')
        class NoVault:
            def put(self,*args):raise AssertionError('late callback must not write credentials')
        with self.assertRaisesRegex(HelperError,'superseded'):oauth_store.finish(other,NoVault(),auth['id'],intent['intent_id'],result('TEST-access','TEST-refresh'))
        self.assertEqual(oauth_store.get(self.store,auth['id'])['state'],'authenticating')
    def test_queued_job_never_resumes_and_paused_catalog_remains_editable(self):
        fx=queue_fixture.QueueTests();fx.setUp();self.addCleanup(fx.doCleanups)
        with sn.snapshot(fx.store.path) as (copy,_):sn.restore_database(copy,self.store.path)
        fx.store=self.store;row=fx.enqueue();saved=self.make();self.backup.restore(saved)
        self.assertIsNone(q.claim(self.store,'worker',clock=fx.clock));self.assertEqual(q.get(self.store,row['id'])['state'],'needs_review')
        source=self.store.list()['items'][0];self.assertEqual(self.store.update(source['id'],1,{'title':'editable'})['title'],'editable')
    def test_cancel_and_timeout_leave_target_database_unchanged(self):
        target=self.root/'destination.db'
        with closing(sqlite3.connect(self.store.path)) as source,closing(sqlite3.connect(target)) as dest:
            dest.execute('CREATE TABLE sentinel(value)');dest.execute('INSERT INTO sentinel VALUES(42)');dest.commit()
            cancel=threading.Event();cancel.set()
            for opts in ({'cancel':cancel},{'timeout':-1}):
                with self.assertRaisesRegex(ValueError,'отменено'):sn.copy_database(source,dest,**opts)
                self.assertEqual(dest.execute('SELECT value FROM sentinel').fetchone()[0],42)
    def test_gate_thread_and_process_timeout_release_and_independent_workspace(self):
        entered=threading.Event();release=threading.Event()
        def hold():
            with gate(self.store.root):entered.set();release.wait(5)
        thread=threading.Thread(target=hold);thread.start();self.assertTrue(entered.wait(2))
        try:
            with self.assertRaises(ValueError):
                with gate(self.store.root,timeout=.05):pass
            with gate(self.root/'other',timeout=.05):pass
            code="import sys;sys.path.insert(0,sys.argv[1]);from content.db_lock import gate\ntry:\n with gate(sys.argv[2],timeout=.05):sys.exit(3)\nexcept ValueError:sys.exit(0)"
            proc=subprocess.run([sys.executable,'-B','-c',code,str(ROOT/'core'),str(self.store.root)],capture_output=True,timeout=3)
            self.assertEqual(proc.returncode,0,proc.stderr)
        finally:release.set();thread.join(5)
        with gate(self.store.root):
            with gate(self.store.root):self.assertTrue(self.store.get(self.material['id']))
    def test_missing_media_report_and_required_before_backup(self):
        sha=assets.put(self.store,b'original','text/plain');(self.store.root/'assets'/sha).unlink();saved=self.make();self.edit()
        with zipfile.ZipFile(saved) as z:self.assertEqual(json.loads(z.read(sn.MANIFEST))['omitted_media'],['assets/'+sha])
        with patch.object(self.backup,'make',side_effect=OSError('TEST disk full')):
            with self.assertRaisesRegex(BackupError,'до восстановления'):self.backup.restore(saved)
        self.assertEqual(self.store.get(self.material['id'])['title'],'TEST changed')
        restored=self.backup.restore(saved);self.assertEqual(restored['content']['omitted_media'],1)
    def test_unsafe_destination_fails_before_current_files_change(self):
        sha=assets.put(self.store,b'original','text/plain');saved=self.make();self.edit()
        asset=self.store.root/'assets'/sha;asset.unlink();target=self.root/'outside';target.write_bytes(b'outside');asset.symlink_to(target)
        with self.assertRaisesRegex(BackupError,'ссылку'):self.backup.restore(saved)
        self.assertEqual(target.read_bytes(),b'outside');self.assertEqual(self.store.get(self.material['id'])['title'],'TEST changed')
    def test_ordinary_content_folder_without_database_is_preserved(self):
        other=self.root/'ordinary';(other/'Контент').mkdir(parents=True);note=other/'Контент/note.md';note.write_text('original')
        backup=Backup(self.cfg,self.state,other);saved=backup.make(self.copies,fmt='zip')['path'];note.write_text('changed');backup.restore(saved)
        self.assertEqual(note.read_text(),'original')

if __name__=='__main__':unittest.main()
