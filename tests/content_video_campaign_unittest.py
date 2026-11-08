"""Shared video campaigns, exact QA references and bounded offline export."""
import base64
import hashlib
import http.cookiejar
import io
import json
import sys
import unittest
import urllib.request
import uuid
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import content_captions_unittest as fixtures
from content import captions,distribution,manual,packages,queue,social_store,video_campaigns,video_review,video_store


class VideoCampaignTests(unittest.TestCase):
    def setUp(self):
        fixtures.CaptionTests.setUp(self)
        with self.store.db() as c:self.video_id=c.execute('SELECT video_id FROM content_video_imports').fetchone()[0]
        def add(kind,data):return social_store.save(self.store,{'kind':kind,'data':data,'request_key':uuid.uuid4().hex})
        con=add('connections',{'name':'TEST'});who=add('identities',{'name':'TEST','url':'https://linkedin.com/in/test'})
        self.bindings=[]
        for suffix in ('one','two'):
            dest=add('destinations',{'name':'TEST '+suffix,'url':'https://linkedin.com/in/'+suffix})
            self.bindings.append(add('bindings',{'name':suffix,'connection_id':con['id'],'identity_id':who['id'],'destination_id':dest['id']})['id'])
        captions.save(self.store,self.args)
        self.qa_args={'import_id':'fake-import','expected_revision':0,'video_revision':1,'reviewer':'TEST','note':'TEST only',
                      'checks':{key:True for key in video_review.CHECKS},'caption_language':'ru','caption_revision':1,'decision':'accepted','request_key':'qa'}
        video_review.save(self.store,self.qa_args)
        self.campaign_args={'source_kind':'video','id':self.video_id,'source_revision':1,'render_import_id':'fake-import','review_revision':1,'binding_ids':self.bindings,'request_key':'campaign'}

    def test_shared_campaign_and_idempotency_without_fake_article(self):
        camp=distribution.create(self.store,self.campaign_args)
        self.assertIsNone(camp['material_id']);self.assertEqual(camp['video_id'],self.video_id)
        self.assertEqual(len(camp['variants']),2);self.assertEqual(camp['source']['caption_ref']['revision'],1)
        self.assertEqual(distribution.create(self.store,self.campaign_args),camp)
        self.assertEqual(distribution.listing(self.store,self.video_id,'video')[0]['id'],camp['id'])
        with self.store.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM materials').fetchone()[0],0)
        self.assertEqual(len(video_campaigns.choices(self.store,self.video_id)),1)

    def test_wrong_render_review_or_source_rejected(self):
        other=video_store.create(self.store,{'data':{'title':'OTHER'},'request_key':'other'})
        for patch in [{'review_revision':2},{'render_import_id':'missing'},{'id':other['id']},{'source_kind':'bogus'},{'id':self.video_id,'source_revision':2}]:
            with self.assertRaises(ValueError):distribution.create(self.store,{**self.campaign_args,**patch})

    def test_changed_captions_or_review_blocks_package_but_keeps_history(self):
        camp=distribution.create(self.store,self.campaign_args);variant=camp['variants'][0]
        args={'variant_id':variant['id'],'expected_revision':1};prepared=packages.prepare(self.store,args)
        captions.save(self.store,{**self.args,'expected_revision':1,'request_key':'edit','cues':[{**self.cues[0],'text':'Changed'}]})
        self.assertEqual(video_campaigns.choices(self.store,self.video_id),[])
        with self.assertRaisesRegex(ValueError,'stale_video'):packages.prepare(self.store,args)
        video_review.save(self.store,{**self.qa_args,'expected_revision':1,'caption_revision':2,'request_key':'review-new'})
        with self.assertRaisesRegex(ValueError,'stale_video'):manual.export(self.store,camp['id'])
        with self.store.db() as c:self.assertEqual(json.loads(c.execute('SELECT snapshot FROM content_packages WHERE hash=?',(prepared['hash'],)).fetchone()[0]),prepared['payload'])
        self.assertEqual(distribution.create(self.store,self.campaign_args)['id'],camp['id'])
        new=distribution.create(self.store,{**self.campaign_args,'review_revision':2,'request_key':'campaign-new'})
        self.assertNotEqual(new['id'],camp['id'])

    def test_zip_has_one_descriptor_and_exact_caption_files_without_paths(self):
        camp=distribution.create(self.store,self.campaign_args);result=manual.export(self.store,camp['id'])
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['b64']))) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            for name,digest in manifest['files'].items():self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(),digest)
            self.assertEqual(sum(n=='media/video.json' for n in archive.namelist()),1)
            self.assertIn('последний'.encode(),archive.read('media/captions.vtt'))
            for item in manifest['packages']:
                payload=json.loads(archive.read('destinations/'+item['variant_id']+'/package.json'))
                self.assertEqual(payload['format'],'video');self.assertEqual(payload['assets'][0]['sha256'],self.asset.stem)
                self.assertNotIn(str(self.store.root),json.dumps(payload));self.assertNotIn('asset_path',json.dumps(payload))
            self.assertIn('В ZIP его нет'.encode(),archive.read('README.txt'))

    def test_manual_receipt_and_api_queue_remain_distinct(self):
        camp=distribution.create(self.store,self.campaign_args);variant=camp['variants'][0]
        prepared=packages.prepare(self.store,{'variant_id':variant['id'],'expected_revision':1})
        report=manual.report(self.store,{'package_hash':prepared['hash'],'permalink':'https://linkedin.com/feed/update/TEST'})
        self.assertEqual(report['state'],'reported_published')
        with self.assertRaisesRegex(ValueError,'очередь видео ещё не подключена'):
            queue.record_scope_from_trusted_ui(self.store,[prepared['hash']],'TEST',200,clock=lambda:100)
        with self.store.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM content_outbox').fetchone()[0],0)

    def test_asset_mutation_and_archived_script_block_new_export(self):
        camp=distribution.create(self.store,self.campaign_args)
        self.asset.write_bytes(b'ALTERED')
        with self.assertRaisesRegex(ValueError,'stale_video'):manual.export(self.store,camp['id'])
        self.assertEqual(distribution.get(self.store,camp['id'])['source']['asset']['sha256'],self.asset.stem)

    def test_authenticated_video_download_exact_bytes_and_caption(self):
        camp=distribution.create(self.store,self.campaign_args)
        url=video_campaigns.preview(self.store,camp['id'])['url'];base=url.split('/open/')[0]
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.assertIn(b'/download',opener.open(url).read())
        request=urllib.request.Request(base+'/download',method='HEAD');response=opener.open(request)
        self.assertIn('attachment;',response.headers['Content-Disposition']);self.assertEqual(response.read(),b'')
        self.assertEqual(opener.open(base+'/download').read(),self.asset.read_bytes())
        self.assertIn('последний'.encode(),opener.open(base+'/captions.vtt').read())


if __name__=='__main__':unittest.main()
