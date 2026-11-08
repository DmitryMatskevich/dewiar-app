"""Manual excerpts stay exact, optional times never become verified alignment."""
import base64
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore,SCHEMA_VERSION
from content import source_records as sr,source_segments as ss,video_store as vs,video_exchange as ex,export,import_package

class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(self.tmp.name)
        self.row=self.store.create({'title':'TEST','body':'Editorial'},'article')
        self.row=sr.change(self.store,{'id':self.row['id'],'expected_revision':1,'operation':'add','note':{'title':'TEST transcript','text':'Первый фрагмент. Повтор. Второй фрагмент. Повтор. 😀 Конец.'},'request_key':'source'}, {})
        self.source=sr.listing(self.store,{'id':self.row['id']})[0]
        self.items=[{'id':'first','quote':'Повтор.','occurrence':2,'start_us':0,'end_us':1250000,'speaker_label':'Спикер 1'}, {'id':'last','quote':'😀 Конец.','occurrence':1,'start_us':None,'end_us':None,'speaker_label':None}]
    def save(self,items=None,key='segments',expected=None,source=None):
        args={'id':self.row['id'],'expected_revision':expected or self.row['revision'],'source_id':source or self.source['source_id'],'operation':'segments','segments':self.items if items is None else items,'request_key':key}
        return sr.change(self.store,args,{})
    def current_source(self):return sr.listing(self.store,{'id':self.row['id']})[0]
    def get(self,source,revision):return ss.get(self.store,{'id':self.row['id'],'revision':revision,'source_id':source['source_id']})
    def test_save_preserves_original_and_creates_revision_with_unknown_not_zero(self):
        original=self.store.sources(self.row['id'],2)[0];self.row=self.save();source=self.current_source()
        self.assertNotEqual(source['source_id'],self.source['source_id']);self.assertEqual(source['sha256'],self.source['sha256'])
        saved=self.get(source,3);self.assertEqual(saved['items'],self.items)
        self.assertIsNone(saved['annotation']['media_ref']);self.assertFalse(saved['annotation']['alignment_verified'])
        self.assertEqual(saved['annotation']['timing_origin'],'editor_declared');self.assertIsNone(saved['items'][1]['start_us'])
        self.assertEqual(self.store.sources(self.row['id'],2)[0],original);self.assertEqual(self.store.get(self.row['id'])['body'],'Editorial')
        self.assertNotIn('segments_v1',source);self.assertEqual(source['segment_count'],2)
    def test_retries_conflicts_and_remove_annotation_keep_old_history(self):
        saved=self.save();self.assertEqual(self.save(),saved)
        with self.assertRaisesRegex(ValueError,'request_conflict'):self.save([],key='segments')
        with self.assertRaisesRegex(ValueError,'revision_conflict'):self.save([],key='other')
        self.row=saved;source=self.current_source();self.row=self.save([],key='clear',source=source['source_id'])
        self.assertEqual(self.current_source()['source_id'],self.source['source_id']);self.assertEqual(self.get(source,3)['items'],self.items)
        restored=self.store.update(self.row['id'],4,{},restore=3);self.assertEqual(restored['revision'],5)
        self.assertEqual(self.current_source()['segment_count'],2)
    def test_rejects_missing_quote_overlap_partial_times_bool_and_forged_verified(self):
        changes=[{'quote':'not in original'},{'occurrence':3},{'start_us':True},{'end_us':None},{'end_us':86400000001}]
        for change in changes:
            items=copy.deepcopy(self.items);items[0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):self.save(items,key=str(change))
        overlap=copy.deepcopy(self.items);overlap[1].update(start_us=1000000,end_us=2000000)
        with self.assertRaisesRegex(ValueError,'пересечений'):self.save(overlap,key='overlap')
        annotation=ss.annotation('Hello',[{'id':'s','quote':'Hello','occurrence':1,'start_us':None,'end_us':None,'speaker_label':None}])
        for change in [{'alignment_verified':True},{'text_sha256':'x'*64},{'media_ref':{'duration_us':1000000}},{'format':True}]:
            with self.subTest(change=change),self.assertRaises(ValueError):ss.validate('Hello',{**annotation,**change})
        self.assertEqual(self.store.get(self.row['id'])['revision'],2)
    def test_zip_four_roundtrip_and_video_handoff_keep_attribution(self):
        self.row=self.save();package=export.package(self.store,self.row['id'],3)
        self.assertEqual(package['manifest']['schema_version'],4)
        imported=import_package.import_package(self.store,{'b64':package['b64'],'request_key':'imported'})
        source=self.store.sources(imported['id'],1)[0]
        self.assertEqual(source['segments_v1']['timing_origin'],'package_claimed');self.assertEqual(source['attribution'],'package_claimed')
        self.assertFalse(source['segments_v1']['alignment_verified']);self.assertEqual(source['segments_v1']['items'],self.items)
        video=vs.create(self.store,{'request_key':'video','source':{'kind':'article','id':self.row['id'],'revision':3},'data':{'scenes':[{'id':'s','title':'test','duration_us':2000000}]}})
        pack=ex.handoff(self.store,{'id':video['id'],'revision':1})
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(pack['base64']))) as z:
            sources=json.loads(z.read('source-records.json'));self.assertEqual(sources['items'][0]['snapshot']['segments_v1']['items'],self.items)
    def test_bad_package_annotations_and_wrong_version_rejected(self):
        self.row=self.save();package=export.package(self.store,self.row['id'],3)
        import hashlib
        for target in ['schema','verification']:
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(package['b64']))) as z:files={n:z.read(n) for n in z.namelist()}
            manifest=json.loads(files['manifest.json'])
            if target=='schema':manifest.update(schema_version=2,format='block-editor-export-v2')
            else:
                sources=json.loads(files['sources.json']);sources[0]['segments_v1']['alignment_verified']=True;files['sources.json']=json.dumps(sources).encode();manifest['files']['sources.json']=hashlib.sha256(files['sources.json']).hexdigest()
            files['manifest.json']=json.dumps(manifest).encode();buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w') as z:
                for name,value in files.items():z.writestr(name,value)
            with self.assertRaises(ValueError):import_package.import_package(self.store,{'b64':base64.b64encode(buf.getvalue()).decode(),'request_key':'bad-'+target})
    def test_schema22_upgrade_preserves_plain_sources_and_old_packages(self):
        before=export.package(self.store,self.row['id'],2)
        with self.store.db() as c:
            originals=[tuple(r) for r in c.execute('SELECT * FROM content_source_records')];c.execute('PRAGMA user_version=22')
        self.store=ContentStore(self.tmp.name)
        with self.store.db() as c:
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM content_source_records')],originals)
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
        self.assertEqual(export.package(self.store,self.row['id'],2),before)
        self.assertEqual(self.get(self.source,2)['items'],[])

if __name__=='__main__':unittest.main()
