"""Isolated content tests: no Engine, settings, networking or model imports."""
import concurrent.futures
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from content.store import ContentStore
from content.commands import dispatch

class ContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=ContentStore(self.root)
    def create(self, **extra):
        return self.store.create({'title':'ЦОД и эксплуатация','body':'Исходный текст','site_id':'site-dcnt-ru',**extra},extra.get('title','fixture'))
    def test_create_reopen_summary_and_cyrillic_search(self):
        a=self.create();b=ContentStore(self.root).get(a['id']);self.assertEqual(a,b)
        rows=self.store.list(query='ЭКСПЛУАТАЦИЯ');self.assertEqual(rows['total'],1);self.assertNotIn('body',rows['items'][0])
        self.assertEqual(len(self.store.sites()),5)
    def test_create_retry_and_conflicting_key(self):
        a=self.create();self.assertEqual(a,self.create())
        with self.assertRaisesRegex(ValueError,'request_conflict'):self.store.create({'title':'Другая'},'fixture')
    def test_revisions_conflict_archive_restore(self):
        a=self.create();b=self.store.update(a['id'],1,{'body':'Ручная правка'})
        with self.assertRaisesRegex(ValueError,'revision_conflict'):self.store.update(a['id'],1,{'body':'Устарело'})
        self.assertEqual(self.store.revision(a['id'],1)['body'],'Исходный текст')
        c=self.store.update(a['id'],b['revision'],{},archived=True);self.assertEqual(self.store.list()['total'],0)
        d=self.store.update(a['id'],c['revision'],{},archived=False);self.assertEqual(self.store.list()['total'],1)
        e=self.store.update(a['id'],d['revision'],{},restore=1)
        self.assertEqual(e['body'],'Исходный текст');self.assertEqual(e['revision'],5)
    def test_invalid_inputs_leave_no_rows(self):
        for data in [{'title':''},{'title':'x','site_id':'other'},{'title':'x','kind':'video'},{'title':'x','body':'я'*600000},{'title':'x','unknown':1}]:
            with self.assertRaises(ValueError):self.store.create(data,'key')
        self.assertEqual(self.store.list()['total'],0)
    def test_concurrent_revision_only_one_wins(self):
        a=self.create()
        def write(i):
            try:return self.store.update(a['id'],1,{'body':str(i)})['revision']
            except ValueError:return 'conflict'
        with concurrent.futures.ThreadPoolExecutor(2) as ex:results=list(ex.map(write,[1,2]))
        self.assertCountEqual(results,[2,'conflict'])
    def test_transaction_rolls_back_revision_failure(self):
        a=self.create();original=self.store._record
        def fail(*args):raise RuntimeError('injected')
        self.store._record=fail
        with self.assertRaises(RuntimeError):self.store.update(a['id'],1,{'body':'lost'})
        self.store._record=original;self.assertEqual(self.store.get(a['id']),a)
    def test_unknown_future_schema_not_reset(self):
        with sqlite3.connect(self.store.path) as c:c.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):ContentStore(self.root)
        with sqlite3.connect(self.store.path) as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],99)
    def test_dispatch_and_exact_domains(self):
        a=dispatch(self.store,'content_create',{'title':'Тест','request_key':'x'})
        self.assertEqual(dispatch(self.store,'content_get',{'id':a['id']}),a)
        self.assertEqual({x['domain'] for x in dispatch(self.store,'content_sites',{})},{'dcnt.ru','ockc.ru','osk.ru','lanmaster.ru','landmaster.ru'})
        with self.assertRaises(ValueError):dispatch(self.store,'content_archive',{'id':a['id'],'expected_revision':1,'archived':'yes'})
        with self.assertRaises(ValueError):self.store.update(a['id'],True,{'title':'x'})
    def test_noop_and_pagination(self):
        a=self.create();self.assertEqual(self.store.update(a['id'],1,{}),a)
        self.create(title='Ещё материал');self.assertEqual(len(self.store.list(limit=1)['items']),1)
        self.assertEqual(self.store.list(offset=2)['items'],[])
        with self.assertRaises(ValueError):self.store.list(limit=101)

    def test_missing_restore_revision_rejected(self):
        a=self.create()
        with self.assertRaises(ValueError):dispatch(self.store,'content_restore_revision',{'id':a['id'],'expected_revision':1})
    def test_parallel_create_same_key_only_one_material(self):
        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            rows=list(ex.map(lambda _:self.store.create({'title':'Одна запись'},'shared'),range(4)))
        self.assertEqual(len({r['id'] for r in rows}),1)
        self.assertEqual(self.store.list()['total'],1)

if __name__=='__main__':unittest.main()
