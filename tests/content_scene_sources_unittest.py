"""Scene citations use exact pinned records. Temporary SQLite only; no Engine/network."""
import base64
import copy
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
from content import source_records as sr,video_store as vs,video_exchange as ex


class SceneSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(self.tmp.name)
        self.article=self.store.create({'title':'TEST article','body':'Editorial narrative'},'article')
        self.article=sr.change(self.store,{'id':self.article['id'],'expected_revision':1,'operation':'add','note':{'title':'TEST source','text':'First excerpt. Second excerpt.'},'request_key':'add'},{})
        self.original=sr.listing(self.store,{'id':self.article['id']})[0]['source_id']
        self.items=[{'id':'first','quote':'First excerpt.','occurrence':1,'start_us':0,'end_us':12000000,'speaker_label':'Editor'}, {'id':'second','quote':'Second excerpt.','occurrence':1,'start_us':None,'end_us':None,'speaker_label':None}]
        self.article=sr.change(self.store,{'id':self.article['id'],'expected_revision':2,'source_id':self.original,'operation':'segments','segments':self.items,'request_key':'annotate'},{})
        self.source=sr.listing(self.store,{'id':self.article['id']})[0]['source_id']
        self.refs=[{'source_id':self.source,'segment_id':'first'}]
        self.scene={'id':'scene','title':'Scene','narration':'Independent wording','duration_us':5000000,'source_segments':self.refs}
        self.row=self.create('video')
    def create(self,key,scene=None):
        return vs.create(self.store,{'request_key':key,'source':{'kind':'article','id':self.article['id'],'revision':self.article['revision']},'data':{'scenes':[scene or self.scene]}})
    def save(self,scenes,expected=None):
        return vs.update(self.store,{'id':self.row['id'],'expected_revision':expected or self.row['revision'],'patch':{'scenes':scenes}})
    def test_create_update_retry_keep_exact_refs_and_independent_narration_duration(self):
        self.assertEqual(self.create('video'),self.row)
        self.assertEqual(self.row['scenes'][0]['source_segments'],self.refs)
        self.assertEqual(self.row['scenes'][0]['duration_us'],5000000)
        self.assertEqual(self.row['scenes'][0]['narration'],'Independent wording')
        changed=copy.deepcopy(self.row['scenes']);changed[0]['narration']='Edited narration'
        self.row=self.save(changed);self.assertEqual(self.row['revision'],2)
        self.assertEqual(self.row['scenes'][0]['source_segments'],self.refs)
        self.assertEqual(self.save(changed),self.row)
    def test_invalid_unknown_foreign_duplicate_and_empty_references(self):
        bad=[None,{},[{}],[{'source_id':self.source,'segment_id':True}],self.refs*2,[{'source_id':self.source,'segment_id':'missing'}],[{'source_id':self.original,'segment_id':'first'}],self.refs*21,[{**self.refs[0],'quote':'forged'}]]
        for refs in bad:
            with self.subTest(refs=refs),self.assertRaises(ValueError):self.save([{**self.scene,'source_segments':refs}])
        self.assertEqual(vs.get(self.store,self.row['id']),self.row)
        with self.assertRaisesRegex(ValueError,'не закреплён'):vs.create(self.store,{'request_key':'standalone','data':{'title':'Standalone','scenes':[self.scene]}})
        self.row=self.save([{**self.scene,'source_segments':[]}]);self.assertNotIn('source_segments',self.row['scenes'][0])
    def test_article_reannotation_never_rebinds_existing_video(self):
        changed=copy.deepcopy(self.items);changed[0]['end_us']=9000000
        self.article=sr.change(self.store,{'id':self.article['id'],'expected_revision':3,'source_id':self.source,'operation':'segments','segments':changed,'request_key':'reannotate'},{})
        other=sr.listing(self.store,{'id':self.article['id']})[0]['source_id']
        self.assertNotEqual(other,self.source)
        with self.assertRaisesRegex(ValueError,'не закреплён'):self.save([{**self.scene,'source_segments':[{'source_id':other,'segment_id':'first'}]}])
        result=vs.dispatch(self.store,'content_video_source_segments',{'id':self.row['id'],'revision':1,'source_id':self.source})
        self.assertEqual(result['items'],self.items);self.assertFalse(result['alignment_verified']);self.assertIsNone(result['media_ref'])
        with self.assertRaisesRegex(ValueError,'не входит'):vs.dispatch(self.store,'content_video_source_segments',{'id':self.row['id'],'source_id':other})
    def test_remove_restore_reel_archive_and_stale_edit(self):
        self.row=self.save([{**self.scene,'source_segments':[]}])
        with self.assertRaisesRegex(ValueError,'revision_conflict'):self.save([self.scene],expected=1)
        self.row=vs.update(self.store,{'id':self.row['id'],'expected_revision':2,'revision':1},restore=True)
        self.assertEqual(self.row['scenes'][0]['source_segments'],self.refs)
        reel=vs.create(self.store,{'request_key':'reel','source':{'kind':'video','id':self.row['id'],'revision':3,'scene_ids':['scene']}})
        self.assertEqual(reel['scenes'],self.row['scenes']);self.assertEqual(reel['source_record_ids'],[self.source])
        archived=vs.update(self.store,{'id':self.row['id'],'expected_revision':3,'archived':True},archive=True)
        self.assertEqual(archived['scenes'],self.row['scenes'])
        self.assertEqual(vs.get(self.store,self.row['id'],1)['scenes'][0]['source_segments'],self.refs)
    def test_handoff_has_resolvable_exact_refs_without_changing_planned_clock(self):
        pack=ex.handoff(self.store,{'id':self.row['id'],'revision':1})
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(pack['base64']))) as z:
            hand=json.loads(z.read('handoff.json'));records=json.loads(z.read('source-records.json'))
            self.assertEqual(hand['storyboard'][0]['source_segments'],self.refs)
            self.assertEqual(hand['storyboard'][0]['end_us'],5000000)
            self.assertEqual(records['items'][0]['snapshot']['segments_v1']['items'][0]['end_us'],12000000)
            for name,item in json.loads(z.read('manifest.json'))['files'].items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),item['sha256'])
        self.assertEqual(ex.handoff(self.store,{'id':self.row['id'],'revision':1}),pack)
    def test_schema23_migration_preserves_old_json_request_and_package_bytes(self):
        old=self.create('legacy',{k:v for k,v in self.scene.items() if k!='source_segments'})
        pack=ex.handoff(self.store,{'id':old['id'],'revision':1})
        with self.store.db() as c:
            raw=c.execute('SELECT snapshot FROM content_video_revisions WHERE id=?',(old['id'],)).fetchone()[0]
            key=c.execute("SELECT result FROM request_keys WHERE key='legacy'").fetchone()[0]
            c.execute('PRAGMA user_version=23')
        self.store=ContentStore(self.tmp.name)
        with self.store.db() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
            self.assertEqual(c.execute('SELECT snapshot FROM content_video_revisions WHERE id=?',(old['id'],)).fetchone()[0],raw)
            self.assertEqual(c.execute("SELECT result FROM request_keys WHERE key='legacy'").fetchone()[0],key)
        self.assertEqual(ex.handoff(self.store,{'id':old['id'],'revision':1}),pack)
        self.assertEqual(vs.update(self.store,{'id':old['id'],'expected_revision':1,'patch':{'scenes':[{**old['scenes'][0],'source_segments':[]}]}}),old)
    def test_many_scenes_share_validated_record_and_list_omits_annotations(self):
        scenes=[{**self.scene,'id':'s'+str(i)} for i in range(200)]
        reads=[]
        from content.scene_sources import validate
        with self.store.db() as c:
            c.set_trace_callback(reads.append);cache={}
            for scene in scenes:validate(c,scene['source_segments'],[self.source],cache)
        self.assertEqual(sum(x.startswith('SELECT snapshot FROM content_source_records') for x in reads),1)
        self.row=self.save(scenes)
        listing=vs.dispatch(self.store,'content_video_source_records',{'id':self.row['id']})
        self.assertNotIn('segments_v1',listing['items'][0]);self.assertLess(len(dump(listing)),2000)
        self.assertEqual(len(self.row['scenes']),200)

if __name__=='__main__':unittest.main()
