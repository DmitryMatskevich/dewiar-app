"""Fake-provider tests: no settings, downloads, model runtime or outbound requests."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore
from content import generation as g

class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.store=ContentStore(self.temp.name)
        self.row=self.store.create({'title':'Статья','blocks':[{'id':'edit','type':'paragraph','text':'Исходный','locked':False},{'id':'manual','type':'quote','text':'Авторский','locked':True}]},'material')
        self.calls=0
    def args(self,**extra):return {'id':self.row['id'],'expected_revision':1,'brief':'Уточни формулировку','block_ids':['edit'],'request_key':'gen',**extra}
    def provider(self,system,prompt,cancel):
        self.calls+=1
        yield json.dumps({'replacements':[{'id':'edit','text':'Предложенный текст'}],'issues':['Нужна проверка фактов']})
    def start(self,runner=None,**extra):return g.start(self.store,self.args(**extra),({'id':'fake','model':'TEST','test_only':True},runner or self.provider))['id']
    def wait(self,jid):
        with g._lock:active=g._active.get((str(self.store.path),jid))
        if active:active[1].join(3);self.assertFalse(active[1].is_alive())
        return g.get(self.store,jid)
    def test_proposal_separate_atomic_apply_idempotent(self):
        jid=self.start();job=self.wait(jid)
        self.assertEqual(job['state'],'proposed');self.assertEqual(self.store.get(self.row['id'])['revision'],1)
        applied=g.apply(self.store,jid);self.assertEqual(applied['revision'],2)
        self.assertEqual(applied['blocks'][1],self.row['blocks'][1]);self.assertEqual(g.apply(self.store,jid),applied)
    def test_retry_same_key_one_call_and_changed_brief_conflict(self):
        jid=self.start();self.wait(jid);self.assertEqual(self.start(),jid);self.assertEqual(self.calls,1)
        with self.assertRaisesRegex(ValueError,'request_conflict'):self.start(brief='Другое задание')
    def test_stale_base_and_locked_block(self):
        with self.assertRaisesRegex(ValueError,'Защищённые'):self.start(block_ids=['manual'])
        jid=self.start();self.wait(jid);self.store.update(self.row['id'],1,{'title':'Новая редакция'})
        with self.assertRaisesRegex(ValueError,'revision_conflict'):g.apply(self.store,jid)
        self.assertEqual(g.get(self.store,jid)['state'],'proposed')
    def test_cancellation_discards_late_provider_result(self):
        began=threading.Event();finish=threading.Event()
        def slow(*args):
            began.set();finish.wait(2);yield json.dumps({'replacements':[{'id':'edit','text':'Поздно'}],'issues':[]})
        jid=self.start(slow);self.assertTrue(began.wait(1));g.cancel(self.store,jid);finish.set()
        self.assertEqual(self.wait(jid)['state'],'canceled');self.assertEqual(self.store.get(self.row['id'])['revision'],1)
    def test_bad_response_and_secret_error_not_persisted(self):
        for i,output in enumerate(['not json',json.dumps({'replacements':[{'id':'manual','text':'Нельзя'}],'issues':[]})]):
            jid=self.start(lambda *args:iter([output]),request_key=str(i));self.assertEqual(self.wait(jid)['state'],'failed')
        def error(*args):raise ValueError('secret-api-key: do not persist')
        jid=self.start(error,request_key='error');job=self.wait(jid)
        self.assertEqual(job['state'],'failed');self.assertNotIn('secret-api-key',json.dumps(job))
    def test_apply_failure_rolls_back_job_and_material(self):
        jid=self.start();self.wait(jid)
        previous=self.store._record
        self.store._record=lambda *args:(_ for _ in ()).throw(RuntimeError('injected'))
        try:
            with self.assertRaises(RuntimeError):g.apply(self.store,jid)
        finally:self.store._record=previous
        self.assertEqual(self.store.get(self.row['id'])['revision'],1);self.assertEqual(g.get(self.store,jid)['state'],'proposed')
    def test_expired_running_job_not_retried(self):
        jid=self.start();self.wait(jid)
        with self.store.db() as c:c.execute("UPDATE generation_jobs SET state='running',deadline=? WHERE id=?",(time.time()-1,jid))
        self.assertEqual(g.get(self.store,jid)['state'],'interrupted');self.assertEqual(self.calls,1)

if __name__=='__main__':unittest.main()
