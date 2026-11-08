"""Campaign isolation, package hashes and manual receipts; zero remote writes."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore,dump
from content import social_store as s,distribution as d,packages,manual

class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(self.tmp.name)
        self.article=self.store.create({'title':'Тест','author':'Автор','body':'Исходный материал'},'article')
        self.conn=self.add('connections',{'name':'LinkedIn'})
        self.author=self.add('identities',{'name':'Автор','url':'https://linkedin.com/in/test'})
        self.dest=self.add('destinations',{'name':'Лента','url':'https://linkedin.com/in/test'})
        self.bind=self.add('bindings',{'name':'Автор → лента','connection_id':self.conn['id'],'identity_id':self.author['id'],'destination_id':self.dest['id']})
    def add(self,kind,data):return s.save(self.store,{'kind':kind,'data':data,'request_key':uuid.uuid4().hex})
    def campaign(self,**kwargs):return d.create(self.store,{'id':self.article['id'],'source_revision':1,'binding_ids':[self.bind['id']],'request_key':uuid.uuid4().hex,**kwargs})
    def test_set_snapshot_dedup_retry_and_source_edit(self):
        group=self.add('sets',{'name':'Первый','binding_ids':[self.bind['id']]})
        other=self.add('sets',{'name':'Второй','binding_ids':[self.bind['id']]})
        args={'set_ids':[group['id'],other['id']],'request_key':'campaign'};camp=self.campaign(**args)
        self.assertEqual(len(camp['variants']),1)
        s.save(self.store,{'kind':'sets','id':group['id'],'expected_revision':1,'data':{'binding_ids':[]}})
        self.store.update(self.article['id'],1,{'body':'Изменено'})
        retry=self.campaign(**args);self.assertEqual(retry,camp)
        self.assertEqual(retry['variants'][0]['text'],'Тест\n\nИсходный материал')
    def test_ambiguous_author_and_connections_rejected(self):
        other=self.add('identities',{'name':'Другой','url':'https://linkedin.com/in/other'})
        binding=self.add('bindings',{'name':'Другой → лента','connection_id':self.conn['id'],'identity_id':other['id'],'destination_id':self.dest['id']})
        with self.assertRaisesRegex(ValueError,'разных авторов'):self.campaign(binding_ids=[self.bind['id'],binding['id']])
        conn=self.add('connections',{'name':'Второе подключение'})
        binding=self.add('bindings',{'name':'Другой маршрут','connection_id':conn['id'],'identity_id':self.author['id'],'destination_id':self.dest['id']})
        with self.assertRaisesRegex(ValueError,'разные подключения'):self.campaign(binding_ids=[self.bind['id'],binding['id']])
    def test_variant_conflict_old_package_hash_and_credentials_absent(self):
        camp=self.campaign();v=camp['variants'][0];args={'variant_id':v['id'],'expected_revision':1};p=packages.prepare(self.store,args)
        self.assertEqual(p,packages.prepare(self.store,args));self.assertEqual(p['authorization_status'],'not_authorized')
        self.assertNotIn('credential_ref',dump(p))
        v2=d.update(self.store,{**args,'text':'Отдельный пост','canonical_url':'https://dcnt.ru/articles/test/'})
        with self.assertRaisesRegex(ValueError,'revision_conflict'):d.update(self.store,{**args,'text':'Потеря'})
        p2=packages.prepare(self.store,{**args,'expected_revision':2});self.assertNotEqual(p['hash'],p2['hash'])
        with self.store.db() as c:self.assertEqual(json.loads(c.execute('SELECT snapshot FROM content_packages WHERE hash=?',(p['hash'],)).fetchone()[0]),p['payload'])
        self.assertIn('https://dcnt.ru/articles/test/',p2['payload']['text'])
    def test_target_archive_and_policy_change_block_new_package(self):
        v=self.campaign()['variants'][0]
        s.save(self.store,{'kind':'destinations','id':self.dest['id'],'expected_revision':1,'data':{'rules':'Новые правила'}})
        with self.assertRaisesRegex(ValueError,'stale_target'):packages.prepare(self.store,{'variant_id':v['id'],'expected_revision':1})
        s.save(self.store,{'kind':'destinations','id':self.dest['id'],'expected_revision':2,'data':{},'archived':True})
        with self.assertRaisesRegex(ValueError,'архив'):self.campaign()
    def test_zip_hashes_and_report_is_not_verified(self):
        camp=self.campaign();v=camp['variants'][0];bundle=manual.export(self.store,camp['id'])
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(bundle['b64']))) as z:
            manifest=json.loads(z.read('manifest.json'))
            for name,digest in manifest['files'].items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),digest)
        args={'package_hash':manifest['packages'][0]['hash'],'permalink':'https://linkedin.com/feed/update/test'}
        result=manual.report(self.store,args);self.assertEqual(result['state'],'reported_published');self.assertEqual(result,manual.report(self.store,args))
        current=d.get(self.store,camp['id']);self.assertEqual(current['variants'][0]['status'],'draft');self.assertEqual(current['variants'][0]['receipts'][0]['variant_revision'],1)
        with self.assertRaises(ValueError):manual.report(self.store,{**args,'permalink':'https://facebook.com/posts/test'})
    def test_archive_restore_and_long_source_not_truncated(self):
        self.store.update(self.article['id'],1,{'body':'т'*11000});camp=self.campaign(source_revision=2);v=camp['variants'][0]
        self.assertEqual(v['text'],'')
        with self.assertRaisesRegex(ValueError,'не обрезается'):manual.export(self.store,camp['id'])
        archived=d.archive(self.store,{'campaign_id':camp['id'],'expected_revision':1,'archived':True})
        with self.assertRaisesRegex(ValueError,'архив'):d.update(self.store,{'variant_id':v['id'],'expected_revision':1,'text':'a'})
        self.assertFalse(d.archive(self.store,{'campaign_id':camp['id'],'expected_revision':archived['revision'],'archived':False})['archived'])
    def test_manual_report_is_pinned_and_can_be_withdrawn(self):
        camp=self.campaign();v=camp['variants'][0]
        package=packages.prepare(self.store,{'variant_id':v['id'],'expected_revision':1})
        d.update(self.store,{'variant_id':v['id'],'expected_revision':1,'text':'Новая редакция'})
        args={'package_hash':package['hash'],'permalink':'https://linkedin.com/feed/update/manual-test','note':'Тестовая отметка'}
        report=manual.report(self.store,args)
        self.assertEqual(d.get(self.store,camp['id'])['variants'][0]['receipts'][0]['variant_revision'],1)
        with self.assertRaisesRegex(ValueError,'другим примечанием'):manual.report(self.store,{**args,'note':'Другое'})
        withdraw={'receipt_id':report['id'],'reason':'ТЕСТ — ошибочная ссылка'}
        result=manual.withdraw(self.store,withdraw)
        self.assertEqual(result,manual.withdraw(self.store,withdraw));self.assertEqual(result['state'],'withdrawn')
        current=d.get(self.store,camp['id'])['variants'][0]
        self.assertEqual(current['receipts'][0]['state'],'withdrawn');self.assertEqual(current['revision'],2);self.assertEqual(current['status'],'draft')
        with self.assertRaises(ValueError):manual.withdraw(self.store,{**withdraw,'reason':''})
        fresh=manual.report(self.store,args);self.assertNotEqual(fresh['id'],report['id'])
        with self.store.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM content_manual_receipts').fetchone()[0],2)
    def test_post_permalink_query_preserved_and_foreign_hosts_rejected(self):
        value='https://www.facebook.com/permalink.php?story_fbid=123&id=456'
        self.assertEqual(manual.permalink('facebook',value),value)
        for url in ['https://facebook.com.evil.example/posts/test','https://facebook.com/','https://facebook.com/posts/test?access_token=secret','http://facebook.com/post/test']:
            with self.assertRaises(ValueError):manual.permalink('facebook',url)
    def test_multiple_destinations_and_request_conflict(self):
        group=self.add('destinations',{'name':'Группа','kind':'group','url':'https://linkedin.com/groups/test'})
        bind=self.add('bindings',{'name':'Группа','connection_id':self.conn['id'],'identity_id':self.author['id'],'destination_id':group['id']})
        result=self.campaign(binding_ids=[self.bind['id'],bind['id']],request_key='unique');self.assertEqual(len(result['variants']),2)
        with self.assertRaisesRegex(ValueError,'request_conflict'):self.campaign(request_key='unique')

if __name__=='__main__':unittest.main()
