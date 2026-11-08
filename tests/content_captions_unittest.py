"""Caption parser/store/player contracts. Temp synthetic receipts, no user media."""
import base64
import hashlib
import http.cookiejar
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore,dump
from content import captions as cap,video_store,video_exchange,video_preview


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ContentStore(self.tmp.name);self.addCleanup(video_preview.close_all)
        video=video_store.create(self.store,{'data':{'title':'TEST','scenes':[{'id':'s1','duration_us':2000000}]},'request_key':'video'})
        handoff=video_exchange.handoff(self.store,{'id':video['id'],'revision':1})
        asset=b'FAKE-MEDIA-ONLY-FOR-HTTP-CONTRACT';digest=hashlib.sha256(asset).hexdigest()
        path=self.store.root/'renders'/f'{digest}.mp4';path.parent.mkdir();path.write_bytes(asset);self.asset=path
        receipt={'sha256':digest,'asset_path':f'renders/{digest}.mp4','bytes':len(asset),'duration_us':2000000,'full_decode':'passed'}
        with self.store.db() as c:
            c.execute('INSERT INTO content_video_imports VALUES (?,?,?,?,?,?,?,?,?,?)',('fake-import',video['id'],handoff['hash'],'fake-import','fake','imported',0,'2026-11-08',dump(receipt),None))
        self.cues=[{'id':'a','start_us':0,'end_us':1000000,'text':'ТЕСТ — первая строка'}, {'id':'b','start_us':1000000,'end_us':2000000,'text':'ТЕСТ — последний блок'}]
        self.args={'import_id':'fake-import','language':'ru','expected_revision':0,'cues':self.cues,'request_key':'caption'}

    def test_strict_timing_bounds_overlap_and_precision(self):
        for changed in [{'start_us':-1},{'end_us':0},{'end_us':2000001},{'end_us':1000001},{'start_us':False}]:
            values=[{**self.cues[0],**changed},self.cues[1]]
            with self.assertRaises(ValueError):cap.cues(values,2000000)
        with self.assertRaises(ValueError):cap.cues([self.cues[1],self.cues[0]],2000000)
        with self.assertRaises(ValueError):cap.cues([self.cues[0],{**self.cues[1],'id':'a'}],2000000)
        self.assertEqual(cap.cues(self.cues,2000000)[-1]['end_us'],2000000)

    def test_srt_crlf_bom_roundtrip_and_vtt_escaping(self):
        text='\ufeff1\r\n00:00:00,000 --> 00:00:01,000\r\n<b>ТЕСТ</b> & текст\r\n\r\n2\r\n00:00:01,000 --> 00:00:02,000\r\nПоследний\r\nблок'
        parsed=cap.parse_srt(text,2000000);track={'cues':parsed}
        self.assertEqual(cap.parse_srt(cap.render(track,'srt',plain=True),2000000),parsed)
        vtt=cap.render(track,'vtt');self.assertTrue(vtt.startswith('WEBVTT\n\n'));self.assertIn('&lt;b&gt;ТЕСТ&lt;/b&gt; &amp; текст',vtt);self.assertIn('00:00:02.000',vtt)
        for invalid in ['','WEBVTT\n\n00:00.000 --> 00:00.100\ntest','1\n00:00:00,000 --> 00:00:01,000 position:0\ntext']:
            with self.assertRaises(ValueError):cap.parse_srt(invalid,2000000)

    def test_save_retry_conflict_and_history(self):
        first=cap.save(self.store,self.args);self.assertFalse(first['alignment_verified'])
        self.assertEqual(cap.save(self.store,self.args),first)
        second=cap.save(self.store,{**self.args,'expected_revision':1,'request_key':'edit','cues':[{**self.cues[0],'text':'Изменено'},self.cues[1]]})
        self.assertEqual(second['revision'],2)
        with self.assertRaisesRegex(ValueError,'revision_conflict'):cap.save(self.store,{**self.args,'request_key':'stale'})
        with self.assertRaisesRegex(ValueError,'request_conflict'):cap.save(self.store,{**self.args,'language':'en'})
        with self.store.db() as c:self.assertEqual(json.loads(c.execute('SELECT snapshot FROM content_caption_revisions WHERE revision=1').fetchone()[0]),first)
        self.assertEqual(cap.save(self.store,self.args),first)

    def test_archive_restore_and_exact_export(self):
        cap.save(self.store,self.args)
        args={'import_id':'fake-import','language':'ru','expected_revision':1,'archived':True}
        archived=cap.archive(self.store,args);self.assertEqual(archived['revision'],2)
        with self.assertRaises(ValueError):cap.save(self.store,{**self.args,'request_key':'archived','expected_revision':2})
        with self.assertRaises(ValueError):cap.dispatch(self.store,'content_caption_export',{'import_id':'fake-import','language':'ru','revision':2,'format':'vtt'})
        cap.archive(self.store,{**args,'expected_revision':2,'archived':False})
        with self.assertRaisesRegex(ValueError,'revision_conflict'):cap.exact(self.store,{'import_id':'fake-import','language':'ru','revision':1})
        exported=cap.dispatch(self.store,'content_caption_export',{'import_id':'fake-import','language':'ru','revision':3,'format':'vtt'})
        self.assertEqual(hashlib.sha256(base64.b64decode(exported['base64'])).hexdigest(),exported['sha256'])

    def test_languages_are_independent_and_cannot_rebind_asset(self):
        cap.save(self.store,self.args);cap.save(self.store,{**self.args,'language':'en','request_key':'english','cues':[{**self.cues[0],'text':'TEST English'}]})
        self.assertEqual(len(cap.get(self.store,'fake-import','ru')['track']['cues']),2)
        self.assertEqual(len(cap.get(self.store,'fake-import','en')['track']['cues']),1)
        with self.assertRaises(ValueError):cap.dispatch(self.store,'content_caption_save',{**self.args,'asset_sha256':'wrong'})
        with self.store.db() as c:
            raw=json.loads(c.execute('SELECT receipt FROM content_video_imports').fetchone()[0]);raw['sha256']='0'*64
            c.execute('UPDATE content_video_imports SET receipt=?',(dump(raw),))
        with self.assertRaisesRegex(ValueError,'контрольной сумме'):cap.get(self.store,'fake-import','ru')

    def test_preview_vtt_authenticated_and_revision_pinned(self):
        track=cap.save(self.store,self.args)
        url=video_preview.open_preview(self.store,'fake-import',track=track);base=url.split('/open/')[0]
        with self.assertRaises(urllib.error.HTTPError) as denied:urllib.request.urlopen(base+'/captions.vtt')
        self.assertEqual(denied.exception.code,403)
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        page=opener.open(url).read().decode();self.assertIn('<track kind="subtitles"',page)
        before=opener.open(base+'/captions.vtt').read();self.assertIn('последний'.encode(),before)
        track2=cap.save(self.store,{**self.args,'expected_revision':1,'request_key':'update','cues':[{**self.cues[0],'text':'Другая версия'}]})
        second=video_preview.open_preview(self.store,'fake-import',track=track2);self.assertNotEqual(url,second)
        self.assertEqual(opener.open(base+'/captions.vtt').read(),before)
        self.asset.unlink()
        with self.assertRaises(urllib.error.HTTPError) as changed:opener.open(base+'/media')
        self.assertEqual(changed.exception.code,409)

    def test_text_limits_and_unaccepted_media(self):
        for text in ['','a\n\nb','a\x00b','x'*1001]:
            with self.assertRaises(ValueError):cap.cues([{**self.cues[0],'text':text}],2000000)
        with self.assertRaises(ValueError):cap.cues([],2000000)
        with self.store.db() as c:c.execute("UPDATE content_video_imports SET state='failed'")
        with self.assertRaises(ValueError):cap.get(self.store,'fake-import','ru')


if __name__=='__main__':unittest.main()
