"""Social catalog in temp SQLite; no accounts, authentication or network."""
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore
from content import social_store as s

class SocialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(self.tmp.name)
    def add(self,kind,data,key=None):return s.save(self.store,{'kind':kind,'data':data,'request_key':key or uuid.uuid4().hex})
    def fixture(self,platform='linkedin'):
        host={'linkedin':'linkedin.com','facebook':'facebook.com'}[platform]
        conn=self.add('connections',{'name':'Подключение','platform':platform})
        identity=self.add('identities',{'name':'Автор','platform':platform,'url':f'https://{host}/in/test'})
        dest=self.add('destinations',{'name':'Группа','platform':platform,'kind':'group','url':f'https://{host}/groups/test'})
        bind=self.add('bindings',{'name':'В группу от автора','connection_id':conn['id'],'identity_id':identity['id'],'destination_id':dest['id']})
        return conn,identity,dest,bind
    def test_all_entity_retries_noop_and_secret_fields_rejected(self):
        conn,identity,dest,bind=self.fixture()
        self.add('sets',{'name':'Личные','binding_ids':[bind['id']]})
        for kind,table in s.TABLES.items():
            with self.store.db() as c:
                row=c.execute('SELECT snapshot FROM '+table).fetchone()
                import json
                obj=json.loads(row['snapshot']);key=c.execute('SELECT key FROM request_keys WHERE result=?',(row['snapshot'],)).fetchone()[0]
            data={k:obj[k] for k in s.FIELDS[kind]}
            self.assertEqual(self.add(kind,data,key),obj)
            self.assertEqual(s.save(self.store,{'kind':kind,'id':obj['id'],'expected_revision':1,'data':{}}),obj)
        for secret in ('token','credential_ref','status','capabilities','verification'):
            with self.assertRaises(ValueError):self.add('connections',{'name':'Bad',secret:'secret'})
    def test_names_not_ids_canonical_dedup_and_languages(self):
        first=self.add('identities',{'name':'Одинаковое имя','platform':'x','url':'https://twitter.com/test'})
        self.add('identities',{'name':'Одинаковое имя','platform':'x','url':'https://x.com/test2'})
        self.assertEqual(s.listing(self.store,{'kind':'identities','query':'ОДИНАКОВОЕ','limit':1})['total'],2)
        with self.assertRaisesRegex(ValueError,'уже есть'):self.add('identities',{'name':'Дубликат','platform':'x','url':'https://www.x.com/test/'})
        for url in ('https://x.com.evil.test/user','https://x.com/user?token=secret','http://x.com/u','https://a:b@x.com/u','https://x.com/'):
            with self.assertRaises(ValueError):self.add('identities',{'name':'Bad','platform':'x','url':url})
        self.assertEqual(first['url'],'https://x.com/test')
    def test_cross_platform_and_readiness_not_inferred(self):
        conn,identity,dest,bind=self.fixture();fb=self.fixture('facebook')
        with self.assertRaises(ValueError):self.add('bindings',{'name':'Bad','connection_id':fb[0]['id'],'identity_id':identity['id'],'destination_id':dest['id']})
        self.assertEqual(bind['capabilities'],[]);self.assertEqual(bind['verification'],'unverified');self.assertIsNone(conn['credential_ref'])
        with self.assertRaises(ValueError):s.save(self.store,{'kind':'identities','id':identity['id'],'expected_revision':1,'data':{'url':'https://linkedin.com/in/other'}})
    def test_two_sets_share_binding_archive_references_and_conflict(self):
        conn,identity,dest,bind=self.fixture()
        one=self.add('sets',{'name':'Первый','binding_ids':[bind['id'],bind['id']]});two=self.add('sets',{'name':'Второй','binding_ids':[bind['id']]})
        self.assertEqual(one['binding_ids'],two['binding_ids'])
        changed=s.save(self.store,{'kind':'destinations','id':dest['id'],'expected_revision':1,'data':{'description':'Правка'},'archived':True})
        with self.assertRaisesRegex(ValueError,'revision_conflict'):s.save(self.store,{'kind':'destinations','id':dest['id'],'expected_revision':1,'data':{'name':'Потеря'}})
        with self.store.db() as c:
            with self.assertRaisesRegex(ValueError,'архив'):s.binding_snapshot(c,bind['id'])
        self.assertEqual(s.get(self.store,'sets',one['id'])['binding_ids'],[bind['id']])
        restored=s.save(self.store,{'kind':'destinations','id':dest['id'],'expected_revision':changed['revision'],'data':{},'archived':False})
        self.assertEqual(restored['description'],'Правка')
        with self.store.db() as c:self.assertEqual(s.binding_snapshot(c,bind['id'])['identity']['id'],identity['id'])
    def test_request_conflict_and_reopen(self):
        self.add('connections',{'name':'A'},'same')
        with self.assertRaisesRegex(ValueError,'request_conflict'):self.add('connections',{'name':'B'},'same')
        self.assertEqual(s.listing(ContentStore(self.tmp.name),{'kind':'connections'})['total'],1)

if __name__=='__main__':unittest.main()
