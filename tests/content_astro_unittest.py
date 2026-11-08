import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore
from content.store import dump
from content.sites import astro,base,service

class AstroTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.store=ContentStore(self.root/'store')
        self.site=self.store.sites()[0];self.project=self.root/'astro';(self.project/'src/data').mkdir(parents=True)
        (self.project/'astro.config.mjs').write_text("export default {site: 'https://dcnt.ru'};")
        self.pages=self.project/'src/data/pages.json';self.pages.write_text(json.dumps([{'id':'existing','title':'Сосед','preservedHtml':'<form>Сохранить</form>','proposedUrl':'/articles/existing/'}]))
        (self.project/'src/data/categories.json').write_text(json.dumps([{'id':'1','title':'Новости'}]))
        material=self.store.create({'title':'Новая статья','author':'Автор','body':'Текст'},'m')
        self.edition=base.create(self.store,{'id':material['id'],'source_revision':1,'site_id':self.site['id'],'request_key':'e','data':{'slug':'new','category_id':'1','publication_date':'2026-11-08T10:00:00Z'}})
    def test_prepare_leaves_source_unchanged_and_creates_reproducible_receipt(self):
        service.configure(self.store,{'site_id':self.site['id'],'local_root':str(self.project),'expected_revision':0})
        before=self.pages.read_bytes();args={'edition_id':self.edition['id'],'expected_revision':1}
        dry=service.prepare(self.store,args);self.assertEqual(service.releases(self.store,self.edition['id']),[])
        receipt=service.prepare(self.store,{**args,'dry_run':False});self.assertEqual(receipt,service.prepare(self.store,{**args,'dry_run':False}))
        self.assertEqual(self.pages.read_bytes(),before);self.assertEqual(dry['plan']['package_hash'],receipt['plan']['package_hash'])
        rows=astro.patched_pages(dry['plan'],astro.capture(self.site,self.project));self.assertEqual(rows[0],json.loads(before)[0]);self.assertTrue(rows[1]['wholeWordWrap'])
        self.assertEqual(rows[1]['postDate'],'2026-11-08 13:00:00')
    def test_update_retains_form_and_metadata_outside_managed_fragment(self):
        snap=astro.capture(self.site,self.project);plan=base.prepare(self.edition,self.site,snap,astro.AstroAdapter());rows=astro.patched_pages(plan,snap)
        rows[-1]['preservedHtml']+='<form>Существующая форма</form>';rows[-1]['customMetadata']='keep';self.pages.write_text(json.dumps(rows))
        updated=base.update(self.store,{'edition_id':self.edition['id'],'expected_revision':1,'patch':{'title':'Исправлено'}})
        snap=astro.capture(self.site,self.project);plan=base.prepare(updated,self.site,snap,astro.AstroAdapter());result=astro.patched_pages(plan,snap)
        self.assertTrue(result[-1]['preservedHtml'].endswith('<form>Существующая форма</form>'));self.assertEqual(result[-1]['customMetadata'],'keep');self.assertEqual(result[0],rows[0])
    def test_wrong_domain_slug_collision_and_stale_template(self):
        with self.assertRaisesRegex(ValueError,'Домен'):astro.capture(self.store.sites()[1],self.project)
        snap=astro.capture(self.site,self.project)
        with self.assertRaisesRegex(ValueError,'URL'):base.prepare({**self.edition,'slug':'existing'},self.site,snap,astro.AstroAdapter())
        plan=base.prepare(self.edition,self.site,snap,astro.AstroAdapter());(self.project/'src/template.astro').write_text('changed')
        with self.assertRaisesRegex(ValueError,'stale_baseline'):base.validate_plan(plan,self.edition,self.site,astro.capture(self.site,self.project))
    def test_ockc_uses_own_brand_and_no_dcnt_style_flag(self):
        (self.project/'astro.config.mjs').write_text("export default {site: 'https://ockc.ru'};")
        site=self.store.sites()[1];edition={**self.edition,'site_id':site['id']};plan=base.prepare(edition,site,astro.capture(site,self.project),astro.AstroAdapter())
        record=plan['changes']['record'];self.assertTrue(record['sourceUrl'].startswith('https://ockc.ru/'));self.assertNotIn('wholeWordWrap',record);self.assertEqual(record['categoryIds'],['1'])

    def test_expired_build_cannot_finish_retry(self):
        service.configure(self.store,{'site_id':self.site['id'],'local_root':str(self.project),'expected_revision':0})
        rid=service.prepare(self.store,{'edition_id':self.edition['id'],'expected_revision':1,'dry_run':False})['release_id']
        with self.store.db() as c:c.execute("UPDATE site_releases SET state='building',receipt=? WHERE id=?",(dump({'deadline':0,'build_token':'old'}),rid))
        self.assertEqual(service.release(self.store,rid)['state'],'failed')
        with self.store.db() as c:c.execute("UPDATE site_releases SET state='building',receipt=? WHERE id=?",(dump({'deadline':9999999999,'build_token':'new'}),rid))
        self.assertFalse(service.finish_build(self.store,rid,'old','preview_built',{}))
        self.assertEqual(service.release(self.store,rid)['receipt']['build_token'],'new')
        self.assertTrue(service.finish_build(self.store,rid,'new','preview_built',{'ok':True}))
        self.assertEqual(service.release(self.store,rid)['receipt'],{'ok':True})

if __name__=='__main__':unittest.main()
