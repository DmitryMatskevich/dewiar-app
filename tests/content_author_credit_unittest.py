"""Portable credit history, import isolation, and propagation; temporary data only."""
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
from content.store import ContentStore,dump,SCHEMA_VERSION
from content import authors,video_store,video_exchange,author_credit,diff
from content.export import package
from content.import_package import import_package
from content.sites import base


class CreditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(Path(self.tmp.name)/'Контент')
        self.author=authors.create(self.store,{'name':'ТЕСТ — эксперт','bio':'Биография v1 <script>alert(1)</script>','site_names':{'site-dcnt-ru':'Подпись DCNT','site-ockc-ru':'Подпись ОСКС','site-lanmaster-ru':'LAN подпись'}},'author')
        self.credit=authors.byline(self.store,self.author['id'],1,'site-dcnt-ru')['author_credit']
    def article(self):
        return self.store.create({'title':'ТЕСТ — статья','author':self.credit['byline'],'site_id':'site-dcnt-ru','body':'Текст статьи','author_credit':self.credit},'article')
    def video(self,article):
        return video_store.create(self.store,{'request_key':'video','source':{'kind':'article','id':article['id'],'revision':article['revision']},'data':{'scenes':[{'id':'one','title':'Одна сцена','duration_us':2000000}]}})

    def test_profile_edit_archive_do_not_change_saved_credit(self):
        article=self.article();raw=package(self.store,article['id'],1)['b64']
        changed=authors.update(self.store,self.author['id'],1,{'bio':'Новая биография','name':'Новое имя','site_names':{}})
        authors.update(self.store,self.author['id'],changed['revision'],{},archived=True)
        self.assertEqual(self.store.get(article['id'])['author_credit'],self.credit)
        saved=self.store.update(article['id'],1,{'title':'Правка заголовка'})
        self.assertEqual(saved['author_credit'],self.credit)
        self.assertEqual(package(self.store,article['id'],1)['b64'],raw)
        with self.assertRaisesRegex(ValueError,'revision_conflict'):authors.byline(self.store,self.author['id'],1,'site-dcnt-ru')

    def test_manual_changes_detach_restore_relinks_exact_snapshot(self):
        article=self.article();saved=self.store.update(article['id'],1,{'author':'Ручная подпись'})
        self.assertNotIn('author_credit',saved)
        restored=self.store.update(article['id'],2,{},restore=1)
        self.assertEqual(restored['author_credit'],self.credit)
        moved=self.store.update(article['id'],3,{'site_id':'site-ockc-ru'})
        self.assertNotIn('author_credit',moved)
        self.assertTrue(diff.compare(self.store,article['id'],1,2)['author_credit']['changed'])
        self.assertNotIn('author_credit',self.store.get(article['id']))

    def test_explicit_detach_is_revision_and_conflict_preserves_credit(self):
        article=self.article();saved=self.store.update(article['id'],1,{'author_credit':None})
        self.assertEqual(saved['revision'],2);self.assertNotIn('author_credit',saved)
        with self.assertRaisesRegex(ValueError,'revision_conflict'):self.store.update(article['id'],1,{'author':'Late'})
        self.assertEqual(self.store.revision(article['id'],1)['author_credit'],self.credit)

    def test_forged_bio_and_cross_site_reference_rejected(self):
        bad=copy.deepcopy(self.credit);bad['bio']='Forged'
        with self.assertRaisesRegex(ValueError,'не совпадают'):self.store.create({'title':'x','author':bad['byline'],'site_id':'site-dcnt-ru','author_credit':bad},'bad')
        with self.assertRaisesRegex(ValueError,'другого сайта'):self.store.create({'title':'x','author':self.credit['byline'],'site_id':'site-ockc-ru','author_credit':self.credit},'wrong-site')
        with self.assertRaises(ValueError):author_credit.validate({**self.credit,'format':True},self.credit['byline'])

    def test_copy_export_import_new_workspace_detaches_catalog(self):
        article=self.article();copied=self.store.create({k:article[k] for k in ('title','author','body','site_id','author_credit')},'copy')
        self.assertEqual(copied['author_credit'],self.credit)
        exported=package(self.store,article['id'],1);self.assertEqual(exported['manifest']['schema_version'],3)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported['b64']))) as z:
            self.assertIn('Биография v1',z.read('article.md').decode())
            self.assertNotIn('<script>alert',z.read('article.html').decode())
            self.assertIn('&lt;script&gt;',z.read('article.html').decode())
        other=ContentStore(Path(self.tmp.name)/'other/Контент')
        imported=import_package(other,{'b64':exported['b64'],'request_key':'import'})
        credit=imported['author_credit'];self.assertEqual(credit['bio'],self.credit['bio'])
        self.assertIsNone(credit['catalog_ref']);self.assertEqual(credit['source_catalog_ref'],self.credit['catalog_ref']);self.assertEqual(credit['origin'],'package_claimed')
        self.assertEqual(authors.listing(other)['total'],0)
        roundtrip=package(other,imported['id'],1)
        again=import_package(other,{'b64':roundtrip['b64'],'request_key':'again'})
        self.assertEqual(again['author_credit'],credit)

    def test_article_video_reel_handoff_keep_exact_credit(self):
        article=self.article();video=self.video(article)
        self.store.update(article['id'],1,{'author_credit':None})
        reel=video_store.create(self.store,{'request_key':'reel','source':{'kind':'video','id':video['id'],'revision':1,'scene_ids':['one']}})
        self.assertEqual(video['author_credit'],self.credit);self.assertEqual(reel['author_credit'],self.credit)
        handoff=video_exchange.handoff(self.store,{'id':reel['id'],'revision':1})
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(handoff['base64']))) as z:
            self.assertEqual(json.loads(z.read('video.json'))['author_credit'],self.credit)
        changed=video_store.update(self.store,{'id':video['id'],'expected_revision':1,'patch':{'author':'Ручная'}})
        self.assertNotIn('author_credit',changed)
        restored=video_store.update(self.store,{'id':video['id'],'expected_revision':2,'revision':1},restore=True)
        self.assertEqual(restored['author_credit'],self.credit)

    def test_site_edition_uses_historical_site_specific_byline_and_bio(self):
        article=self.article();authors.update(self.store,self.author['id'],1,{'bio':'New','site_names':{}})
        edition=base.create(self.store,{'id':article['id'],'source_revision':1,'site_id':'site-ockc-ru','request_key':'edition'})
        self.assertEqual(edition['author'],'Подпись ОСКС');self.assertEqual(edition['author_credit']['bio'],self.credit['bio'])
        self.assertEqual(edition['author_credit']['catalog_ref']['revision'],1)
        updated=base.update(self.store,{'edition_id':edition['id'],'expected_revision':1,'patch':{'description':'Independent'}})
        self.assertEqual(updated['author_credit'],edition['author_credit'])
        detached=base.update(self.store,{'edition_id':edition['id'],'expected_revision':2,'patch':{'author_credit':None}})
        self.assertNotIn('author_credit',detached)

    def test_legacy_bytes_keys_and_package_hash_unchanged_on_reopen(self):
        data={'title':'Legacy','author':'Free byline','body':'Old text'}
        row=self.store.create(data,'legacy');before=package(self.store,row['id'],1)['b64']
        with self.store.db() as c:
            snapshot=c.execute('SELECT snapshot FROM revisions WHERE material_id=?',(row['id'],)).fetchone()[0]
            key=dict(c.execute('SELECT * FROM request_keys WHERE key=?',('legacy',)).fetchone())
            c.execute('DROP TABLE content_video_revision_sources')
            c.execute('PRAGMA user_version=20')
        reopened=ContentStore(self.store.root)
        self.assertEqual(reopened.create(data,'legacy'),row);self.assertNotIn('author_credit',reopened.get(row['id']))
        self.assertEqual(package(reopened,row['id'],1)['b64'],before)
        with reopened.db() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
            self.assertEqual(c.execute('SELECT snapshot FROM revisions WHERE material_id=?',(row['id'],)).fetchone()[0],snapshot)
            self.assertEqual(dict(c.execute('SELECT * FROM request_keys WHERE key=?',('legacy',)).fetchone()),key)
        self.assertEqual(reopened.update(row['id'],1,{'author_credit':None})['revision'],1)
        self.assertTrue(list((self.store.root/'backups').glob(f'before-v20-to-v{SCHEMA_VERSION}-*.db')))

    def test_astro_preparation_includes_escaped_bio_without_modifying_source(self):
        from content.sites import astro
        article=self.article();site=self.store.sites()[0]
        project=Path(self.tmp.name)/'site';(project/'src/data').mkdir(parents=True)
        (project/'astro.config.mjs').write_text("export default {site: 'https://dcnt.ru'};")
        pages=project/'src/data/pages.json';pages.write_text('[]')
        (project/'src/data/categories.json').write_text('[{"id":"1","title":"Новости"}]')
        edition=base.create(self.store,{'id':article['id'],'source_revision':1,'site_id':site['id'],'request_key':'astro-edition','data':{'slug':'test','category_id':'1','publication_date':'2026-11-08T10:00:00Z'}})
        plan=base.prepare(edition,site,astro.capture(site,project),astro.AstroAdapter())
        html=plan['changes']['record']['preservedHtml']
        self.assertIn('Об авторе',html);self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)
        self.assertEqual(pages.read_text(),'[]')


if __name__=='__main__':unittest.main()
