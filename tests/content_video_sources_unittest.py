"""Article provenance pinned to video/reel; isolated storage, no media/network."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore,SCHEMA_VERSION,dump
from content import source_records as sr,video_store as vs,video_sources as sources,video_exchange as ex,assets


class VideoSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(self.tmp.name)
        self.article=self.store.create({'title':'TEST article','body':'Editorial body'},'article')
        self.add('First origin','Same source text','https://example.com/one')
        self.add('Second origin','Same source text','https://example.com/two')
    def add(self,title,text,url=''):
        self.article=sr.change(self.store,{'id':self.article['id'],'expected_revision':self.article['revision'],'operation':'add','note':{'title':title,'text':text,'url':url},'request_key':title},{})
    def video(self):
        return vs.create(self.store,{'request_key':'video','source':{'kind':'article','id':self.article['id'],'revision':self.article['revision']},'data':{'scenes':[{'id':'one','title':'TEST','duration_us':1000000}]}})
    def listing(self,row):return sources.dispatch(self.store,'content_video_source_records',{'id':row['id'],'revision':row['revision']})
    def test_same_sha_different_origins_and_article_change_keep_exact_records(self):
        row=self.video();items=self.listing(row)['items'];self.assertEqual(len(items),2)
        self.assertEqual(items[0]['sha256'],items[1]['sha256']);self.assertNotEqual(items[0]['source_id'],items[1]['source_id'])
        self.article=sr.change(self.store,{'id':self.article['id'],'expected_revision':self.article['revision'],'operation':'replace','source_id':items[0]['source_id'],'note':{'title':'replacement','text':'new source'},'request_key':'replace'},{})
        self.assertEqual(self.listing(row)['items'],items)
        content=sources.dispatch(self.store,'content_video_source_read',{'id':row['id'],'source_id':items[0]['source_id'],'offset':5,'limit':6})
        self.assertEqual(content['text'],'source');self.assertEqual(content['total'],16)
        new_id=sr.listing(self.store,{'id':self.article['id']})[0]['source_id']
        with self.assertRaisesRegex(ValueError,'не входит'):sources.dispatch(self.store,'content_video_source_read',{'id':row['id'],'source_id':new_id})
    def test_reel_edit_archive_restore_keep_sources_and_no_manual_forgery(self):
        row=self.video();original=self.listing(row)
        with self.assertRaises(ValueError):vs.update(self.store,{'id':row['id'],'expected_revision':1,'patch':{'source_record_ids':[]}})
        changed=vs.update(self.store,{'id':row['id'],'expected_revision':1,'patch':{'title':'changed'}})
        restored=vs.update(self.store,{'id':row['id'],'expected_revision':2,'revision':1},restore=True)
        archived=vs.update(self.store,{'id':row['id'],'expected_revision':3,'archived':True},archive=True)
        self.assertEqual(self.listing(archived)['items'],original['items'])
        reel=vs.create(self.store,{'request_key':'reel','source':{'kind':'video','id':row['id'],'revision':restored['revision'],'scene_ids':['one']}})
        self.assertEqual(self.listing(reel)['items'],original['items'])
        self.assertEqual(self.listing(row)['items'],original['items'])
    def test_handoff_two_verifies_sources_assets_manifest_and_is_deterministic(self):
        asset=assets.put(self.store,b'<p>Original source</p>','text/html')
        with self.store.db() as c:
            ident=sr.put_in(c,{'text':'Original source','sha256':asset,'asset_id':asset,'source_kind':'html','provenance':'editor_supplied'})
            chosen=sr.ids_in(c,self.article['id'],self.article['revision'])
            self.article=self.store.update(self.article['id'],self.article['revision'],{},_c=c,_source_ids=chosen+[ident])
        row=self.video();args={'id':row['id'],'revision':1};pack=ex.handoff(self.store,args)
        self.assertEqual(ex.handoff(self.store,args),pack)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(pack['base64']))) as z:
            manifest=json.loads(z.read('manifest.json'));handoff=json.loads(z.read('handoff.json'))
            self.assertEqual(handoff['schema'],'dewiar-video-handoff/2')
            for name,item in manifest['files'].items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),item['sha256'])
            records=z.read('source-records.json');self.assertEqual(hashlib.sha256(records).hexdigest(),handoff['source_records_sha256'])
            self.assertEqual(len(json.loads(records)['items']),3);self.assertEqual(z.read('source-assets/'+asset),b'<p>Original source</p>')
            self.assertEqual(handoff['time_basis'],'planned_microseconds');self.assertFalse(handoff['native_project'])
        with self.store.db() as c:self.assertEqual(ex._handoff(c,pack['hash'])['schema'],'dewiar-video-handoff/2')
    def test_missing_original_refuses_incomplete_handoff_without_receipt(self):
        asset=assets.put(self.store,b'original','text/plain')
        with self.store.db() as c:
            ident=sr.put_in(c,{'text':'original','sha256':asset,'asset_id':asset})
            self.article=self.store.update(self.article['id'],self.article['revision'],{},_c=c,_source_ids=[ident])
        row=self.video();(self.store.root/'assets'/asset).unlink()
        with self.assertRaisesRegex(ValueError,'недоступен'):ex.handoff(self.store,{'id':row['id'],'revision':1})
        self.assertEqual(ex.handoffs(self.store,row['id']),[])
    def test_migration_does_not_invent_sources_or_rewrite_legacy_bytes(self):
        row=self.video();row.pop('source_record_ids');row.pop('source_records_status');raw=dump(row)
        with self.store.db() as c:
            c.execute('DELETE FROM content_video_revision_sources')
            c.execute('UPDATE content_videos SET snapshot=?',(raw,));c.execute('UPDATE content_video_revisions SET snapshot=?',(raw,))
            c.execute("UPDATE request_keys SET result=? WHERE command='video_create'",(raw,))
        oldpack=ex.handoff(self.store,{'id':row['id'],'revision':1})
        with self.store.db() as c:
            c.execute('DROP TABLE content_video_revision_sources');c.execute('PRAGMA user_version=21')
        self.store=ContentStore(self.tmp.name)
        with self.store.db() as c:
            self.assertEqual(c.execute('SELECT snapshot FROM content_video_revisions').fetchone()[0],raw)
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
        self.assertEqual(ex.handoff(self.store,{'id':row['id'],'revision':1}),oldpack)
        self.assertEqual(self.listing(row),{'status':'legacy_unavailable','video_revision':1,'items':[]})
        reel=vs.create(self.store,{'request_key':'legacy-reel','source':{'kind':'video','id':row['id'],'revision':1}})
        self.assertEqual(self.listing(reel)['status'],'legacy_unavailable')
        self.assertTrue(list((self.store.root/'backups').glob(f'before-v21-to-v{SCHEMA_VERSION}-*.db')))
    def test_list_and_reader_are_bounded_and_tampered_binding_rejected(self):
        self.add('Large text','x'*50000);row=self.video();info=self.listing(row);self.assertNotIn('text',info['items'][2]);self.assertLess(len(dump(info)),4000)
        ident=info['items'][2]['source_id'];part=sources.dispatch(self.store,'content_video_source_read',{'id':row['id'],'source_id':ident,'offset':40000})
        self.assertEqual(len(part['text']),10000)
        with self.assertRaises(ValueError):sources.dispatch(self.store,'content_video_source_read',{'id':row['id'],'source_id':ident,'limit':20001})
        with self.store.db() as c:c.execute('DELETE FROM content_video_revision_sources WHERE source_id=?',(ident,))
        with self.assertRaisesRegex(ValueError,'не соответствует'):self.listing(row)

if __name__=='__main__':unittest.main()
