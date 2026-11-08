"""Isolated streaming archives; synthetic bytes, no Engine or network/model work."""
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'core'), str(ROOT/'tools')]
from content import archives as ar, snapshots as sn, assets, video_store, video_exchange, oauth_store
from content.store import ContentStore, dump, now
from content.db_lock import gate
from content_test_host import handler
from interface_origin import authenticated_interface


class ArchivesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.store = ContentStore(self.root/'workspace/Контент')
        self.out = self.root/'copies'; self.out.mkdir()
        self.article = self.store.create({'title':'TEST original','body':'TEST content'},'article')
        self.sha = assets.put(self.store,b'TEST immutable source','text/plain')
        self.cancel = threading.Event()
    def create(self, update=lambda **x:None):
        return ar.archive_create(self.store,self.out,uuid.uuid4().hex,self.cancel,update)
    def restore(self, path, update=lambda **x:None):
        return ar.archive_restore(path,self.out,uuid.uuid4().hex,self.cancel,update)
    def service(self):
        service = ar.Service(self.store); self.addCleanup(service.close); return service
    def finish(self, service, ident):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            with service.lock: active=service.active
            if not active: return service.read(ident)
            active[2].join(.05)
        self.fail('Archive worker timeout')
    def rewrite(self, path, change):
        with zipfile.ZipFile(path) as z: entries={n:z.read(n) for n in z.namelist()}
        change(entries); dest=self.out/('modified-'+uuid.uuid4().hex+'.zip')
        with zipfile.ZipFile(dest,'w') as z:
            for k,v in entries.items(): z.writestr(k,v)
        return dest
    def render(self, size):
        video=video_store.create(self.store,{'request_key':'v','data':{'title':'TEST byte fixture','scenes':[{'id':'s1','title':'TEST','duration_us':1000000}]}})
        pack=video_exchange.handoff(self.store,{'id':video['id'],'revision':1})
        folder=self.store.root/'renders'; folder.mkdir(); path=folder/'test-bytes'; digest=hashlib.sha256()
        with path.open('wb') as f:
            remaining=size
            while remaining:
                data=b'\0'*min(ar.CHUNK,remaining);f.write(data);digest.update(data);remaining-=len(data)
        sha=digest.hexdigest(); target=folder/(sha+'.mp4');path.rename(target)
        receipt={'asset_path':'renders/'+target.name,'sha256':sha,'bytes':size,'test_only':True}
        with self.store.db() as c:
            c.execute('INSERT INTO content_video_imports VALUES(?,?,?,?,?,?,?,?,?,?)',('test-import',video['id'],pack['hash'],'import','test','imported',0,now(),dump(receipt),None))
        return target,receipt
    def test_large_file_streaming_snapshot_and_restored_bytes(self):
        path,receipt=self.render(101*ar.CHUNK+17);progress=[]; edited=False
        def update(**values):
            nonlocal edited
            progress.append(values)
            if not edited:
                edited=True;self.store.update(self.article['id'],1,{'title':'TEST changed during copy'})
        original_read=Path.read_bytes
        def bounded_read(p):
            if p.suffix in ('.mp4','.zip'):raise AssertionError('Large files must stream')
            return original_read(p)
        with patch.object(Path,'read_bytes',bounded_read):
            saved=self.create(update); restored=self.restore(saved['path'])
        other=ContentStore(Path(restored['workspace'])/'Контент')
        self.assertEqual(other.get(self.article['id'])['title'],'TEST original')
        self.assertEqual(self.store.get(self.article['id'])['title'],'TEST changed during copy')
        with (other.root/receipt['asset_path']).open('rb') as f:self.assertEqual(hashlib.file_digest(f,'sha256').hexdigest(),receipt['sha256'])
        self.assertEqual(saved['media_count'],2);self.assertGreater(saved['bytes'],100*ar.CHUNK)
        self.assertGreater(len(progress),100);self.assertEqual(progress[-1]['done_bytes'],progress[-1]['total_bytes'])
        with zipfile.ZipFile(saved['path']) as z:self.assertEqual(json.loads(z.read(sn.MANIFEST))['omitted_media'],[])
    def test_missing_changed_file_and_symlink_fail_without_result(self):
        path=self.store.root/'assets'/self.sha
        path.unlink()
        with self.assertRaises(ValueError):self.create()
        path.write_bytes(b'wrong source')
        with self.assertRaises(ValueError):self.create()
        path.unlink();outside=self.root/'outside';outside.write_bytes(b'TEST immutable source');path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'ссылок'):self.create()
        self.assertEqual(list(self.out.iterdir()),[])
    def test_cancel_create_restore_cleanup_and_low_disk(self):
        def stop(**values):self.cancel.set()
        with self.assertRaises(ar.Canceled):self.create(stop)
        self.assertEqual(list(self.out.iterdir()),[]);self.cancel.clear()
        saved=self.create()
        with self.assertRaises(ar.Canceled):self.restore(saved['path'],stop)
        self.assertEqual([p.name for p in self.out.iterdir()],[Path(saved['path']).name]);self.cancel.clear()
        from collections import namedtuple
        usage=namedtuple('usage','total used free')
        with patch.object(ar.shutil,'disk_usage',return_value=usage(10,10,0)):
            with self.assertRaisesRegex(ValueError,'Недостаточно места'):self.create()
    def test_corrupt_missing_extra_future_and_incomplete_archive(self):
        saved=self.create()['path']; original=self.store.get(self.article['id'])
        def report_change(entries,key,value):
            report=json.loads(entries[sn.MANIFEST]);report[key]=value;entries[sn.MANIFEST]=dump(report).encode()
        changes=[lambda e:e.__setitem__(ar.PREFIX+'assets/'+self.sha,b'bad'),lambda e:e.pop(ar.PREFIX+'assets/'+self.sha),
                 lambda e:e.__setitem__('../escape',b'bad'),lambda e:report_change(e,'schema_version',999),
                 lambda e:report_change(e,'omitted_media',['missing']),lambda e:e.__setitem__(sn.DATABASE,b'not sqlite')]
        for change in changes:
            with self.subTest(change=change):
                path=self.rewrite(saved,change)
                with self.assertRaises((ValueError,sqlite3.Error)):self.restore(path)
                self.assertFalse(list(self.out.glob('Dewiar-restored-*')));self.assertFalse(list(self.out.glob('.dewiar-restore-*')))
                self.assertEqual(self.store.get(self.article['id']),original)
        duplicate=self.out/'duplicate.zip'
        with zipfile.ZipFile(saved) as src,zipfile.ZipFile(duplicate,'w') as z:
            for n in src.namelist():z.writestr(n,src.read(n))
            import warnings
            with warnings.catch_warnings():warnings.simplefilter('ignore');z.writestr(sn.MANIFEST,src.read(sn.MANIFEST))
        with self.assertRaisesRegex(ValueError,'повторяющиеся'):self.restore(duplicate)
    def test_restore_pauses_oauth_and_does_not_copy_other_content_files(self):
        auth=oauth_store.create(self.store,'TEST OAuth','auth');oauth_store.begin(self.store,auth['id'],1,'authorize','login')
        (self.store.root/'PRIVATE-NOT-IN-ARCHIVE').write_text('TEST non-catalog sentinel')
        result=self.restore(self.create()['path']);other=ContentStore(Path(result['workspace'])/'Контент')
        self.assertEqual(oauth_store.get(other,auth['id'])['state'],'needs_login')
        self.assertEqual(oauth_store.get(self.store,auth['id'])['state'],'authenticating')
        self.assertFalse((other.root/'PRIVATE-NOT-IN-ARCHIVE').exists());self.assertEqual(assets.read(other,self.sha),b'TEST immutable source')
    def test_service_idempotence_reopen_cancel_and_independent_workspace(self):
        service=self.service();args={'kind':'create','destination':str(self.out),'request_key':'same'}
        row=service.start(args);done=self.finish(service,row['id']);self.assertEqual(done['state'],'completed',done)
        self.assertEqual(service.start(args)['id'],row['id']);self.assertEqual(ar.Service(self.store).read(row['id'])['state'],'completed')
        another=self.root/'other';another.mkdir()
        with self.assertRaisesRegex(ValueError,'Ключ'):service.start({**args,'destination':str(another)})
        entered=threading.Event();release=threading.Event();original=ar.archive_create
        def slow(*a):entered.set();release.wait(5);return original(*a)
        with patch.object(ar,'archive_create',slow):
            active=service.start({**args,'request_key':'cancel'});self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError,'текущей'):service.start({**args,'request_key':'parallel'})
            second=ar.Service(self.store);self.assertEqual(second.read(active['id'])['state'],'running')
            with self.assertRaisesRegex(ValueError,'занят'):second.start({**args,'request_key':'other-process'})
            self.assertEqual(service.cancel(active['id'])['state'],'cancel_requested');release.set()
            self.assertEqual(self.finish(service,active['id'])['state'],'canceled')
        service.close()
        with self.assertRaisesRegex(ValueError,'завершает'):service.start({**args,'request_key':'closed'})
    def test_recovery_and_authentication_before_store_creation(self):
        service=self.service();row=service.start({'kind':'create','destination':str(self.out),'request_key':'old'});self.finish(service,row['id'])
        saved=service.read(row['id']);saved['state']='running';service.write(saved)
        partial=self.out/('.dewiar-content-'+row['id']+'.partial');partial.write_bytes(b'TEST unfinished')
        recovered=ar.Service(self.store);self.addCleanup(recovered.close);self.assertEqual(recovered.read(row['id'])['state'],'interrupted')
        self.assertFalse(partial.exists());self.assertTrue(any(self.out.glob('*.zip')))
        handler_root=self.root/'handler';call=handler(handler_root);self.addCleanup(call.close)
        with self.assertRaisesRegex(ValueError,'авторизованный'):call('content_archive_jobs',{})
        self.assertFalse(handler_root.exists())
        with authenticated_interface():
            result=call('content_archive_jobs',{});self.assertEqual(result['items'],[])
        self.assertEqual(result['workspace'],str(handler_root))
    def test_worker_disk_error_does_not_report_success(self):
        service=self.service()
        with patch.object(ar.os,'link',side_effect=OSError('TEST disk full')):
            row=service.start({'kind':'create','destination':str(self.out),'request_key':'disk-error'});done=self.finish(service,row['id'])
        self.assertEqual(done['state'],'failed');self.assertIsNone(done['result']);self.assertEqual(list(self.out.iterdir()),[])
    def test_crc_failure_and_symlink_entry_never_publish_restore(self):
        path=Path(self.create()['path'])
        with zipfile.ZipFile(path) as z:
            entry=z.getinfo(ar.PREFIX+'assets/'+self.sha)
            offset=entry.header_offset
        # Flip actual stored bytes without repairing the central-directory CRC.
        with path.open('r+b') as f:
            import struct
            f.seek(offset+26);name_len,extra_len=struct.unpack('<HH',f.read(4))
            f.seek(offset+30+name_len+extra_len);first=f.read(1);f.seek(-1,1);f.write(bytes([first[0]^1]))
        with self.assertRaises(zipfile.BadZipFile):self.restore(path)
        self.assertFalse(list(self.out.glob('Dewiar-restored-*')))
        clean=Path(self.create()['path']);linked=self.out/'linked.zip'
        with zipfile.ZipFile(clean) as src,zipfile.ZipFile(linked,'w') as dest:
            for entry in src.infolist():
                if entry.filename.startswith(ar.PREFIX+'assets/'):entry.external_attr=(0o120777<<16)
                dest.writestr(entry,src.read(entry.filename))
        with self.assertRaisesRegex(ValueError,'ссылочные'):self.restore(linked)


if __name__=='__main__':unittest.main()
