"""Local editions and pure adapter contracts; no filesystem site mutation."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore
from content.sites import base

class Adapter:
    name='astro'
    def prepare(self,edition,site,baseline):return {'path':'/articles/'+edition['slug']+'/','title':edition['title']}

class SiteBaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(self.tmp.name)
        self.material=self.store.create({'title':'Тест','body':'Текст'},'m')
        self.args={'id':self.material['id'],'source_revision':1,'site_id':'site-dcnt-ru','request_key':'e','data':{'slug':'test','publication_date':'2026-11-08T12:00:00+03:00','category_id':'1'}}
        self.site=self.store.sites()[0]
    def test_create_edit_archive_revision_conflict(self):
        row=base.create(self.store,self.args);self.assertEqual(base.create(self.store,self.args),row)
        updated=base.update(self.store,{'edition_id':row['id'],'expected_revision':1,'patch':{'title':'Для сайта'}})
        self.assertEqual(updated['revision'],2);self.assertEqual(self.store.get(self.material['id'])['title'],'Тест')
        with self.assertRaisesRegex(ValueError,'revision_conflict'):base.update(self.store,{'edition_id':row['id'],'expected_revision':1,'patch':{}})
        archived=base.update(self.store,{'edition_id':row['id'],'expected_revision':2,'archived':True});self.assertTrue(archived['archived'])
        self.assertFalse(base.update(self.store,{'edition_id':row['id'],'expected_revision':3,'archived':False})['archived'])
    def test_pure_prepare_and_baseline_validation(self):
        row=base.create(self.store,self.args);baseline={'sha256':'abc'};original=copy.deepcopy((row,self.site,baseline))
        before={p:p.stat().st_mtime_ns for p in Path(self.tmp.name).rglob('*')}
        plan=base.prepare(row,self.site,baseline,Adapter());self.assertEqual((row,self.site,baseline),original)
        self.assertEqual(before,{p:p.stat().st_mtime_ns for p in Path(self.tmp.name).rglob('*')})
        self.assertTrue(base.validate_plan(plan,row,self.site,baseline))
        with self.assertRaisesRegex(ValueError,'stale_baseline'):base.validate_plan(plan,row,self.site,{'sha256':'new'})
        with self.assertRaises(ValueError):base.prepare(row,{**self.site,'id':'site-ockc-ru'},baseline,Adapter())
    def test_invalid_date_slug_and_cross_site_update(self):
        for patch in [{'slug':'../admin'},{'publication_date':'2026-11-08T12:00:00'},{'language':'xx'}]:
            with self.assertRaises(ValueError):base.create(self.store,{**self.args,'data':{**self.args['data'],**patch}})
        row=base.create(self.store,self.args)
        with self.assertRaises(ValueError):base.update(self.store,{'edition_id':row['id'],'expected_revision':1,'patch':{'site_id':'site-ockc-ru'}})

if __name__=='__main__':unittest.main()
