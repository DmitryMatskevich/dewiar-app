"""Durable outbox contract with explicit fake evidence and controllable clock."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore
from content import social_store as s,distribution as d,packages,queue as q,reconcile,schedule,worker

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(self.tmp.name);self.t=1800000000.;self.clock=lambda:self.t
        def add(kind,data):return s.save(self.store,{'kind':kind,'data':data,'request_key':uuid.uuid4().hex})
        source=self.store.create({'title':'TEST','body':'post'},'source')
        conn=add('connections',{'name':'TEST'});identity=add('identities',{'name':'TEST','url':'https://linkedin.com/in/test'});dest=add('destinations',{'name':'TEST','url':'https://linkedin.com/in/test'})
        binding=add('bindings',{'name':'TEST','connection_id':conn['id'],'identity_id':identity['id'],'destination_id':dest['id']})
        self.variant=d.create(self.store,{'id':source['id'],'source_revision':1,'binding_ids':[binding['id']],'request_key':'campaign'})['variants'][0]
        self.package=packages.prepare(self.store,{'variant_id':self.variant['id'],'expected_revision':1})
        self.scope=q.record_scope_from_trusted_ui(self.store,[self.package['hash']],'TEST fake trusted UI',self.t+10000,clock=self.clock)
    def iso(self,offset=0):return datetime.fromtimestamp(self.t+offset,timezone.utc).isoformat()
    def enqueue(self,offset=0,window=300):return q.enqueue(self.store,self.package['hash'],self.scope,self.iso(offset),overdue_window=window,clock=self.clock)
    def start(self):
        row=self.enqueue();job=q.claim(self.store,'worker',clock=self.clock);payload=q.begin_send(self.store,job,clock=self.clock);return row,job,payload
    def evidence(self,payload,**extra):return {'remote_id':'remote-test','identity_id':payload['target']['identity']['id'],'destination_id':payload['target']['destination']['id'],'body_sha256':hashlib.sha256(payload['text'].encode()).hexdigest(),**extra}
    def test_concurrent_claim_only_one_and_idempotent_enqueue(self):
        row=self.enqueue();self.assertEqual(row,self.enqueue())
        with ThreadPoolExecutor(2) as pool:claims=list(pool.map(lambda owner:q.claim(self.store,owner,clock=self.clock),['one','two']))
        self.assertEqual(sum(c is not None for c in claims),1)
        with self.assertRaisesRegex(ValueError,'request_conflict'):self.enqueue(10)
    def test_crash_before_and_after_send_boundary(self):
        row=self.enqueue();one=q.claim(self.store,'one',clock=self.clock);self.t+=61
        two=q.claim(self.store,'two',clock=self.clock);self.assertGreater(two['fence'],one['fence'])
        with self.assertRaisesRegex(ValueError,'lease_lost'):q.begin_send(self.store,one,clock=self.clock)
        q.begin_send(self.store,two,clock=self.clock);self.t+=61;self.assertIsNone(q.claim(self.store,'three',clock=self.clock))
        self.assertEqual(q.get(self.store,row['id'])['state'],'publication_unknown')
        with self.assertRaises(ValueError):q.retry(self.store,row['id'],self.scope,self.iso(),clock=self.clock)
    def test_late_result_is_observation_not_overwrite(self):
        row,job,_=self.start();self.t+=61;q.claim(self.store,'other',clock=self.clock)
        self.assertFalse(q.finish_send(self.store,job,{'outcome':'accepted','remote_id':'remote-test'},clock=self.clock))
        self.assertEqual(q.get(self.store,row['id'])['state'],'publication_unknown')
        with self.store.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM content_observations').fetchone()[0],1)
    def test_exact_verification_and_changed_variant_after_send(self):
        row,job,payload=self.start();q.finish_send(self.store,job,{'outcome':'accepted','remote_id':'remote-test'},clock=self.clock)
        d.update(self.store,{'variant_id':self.variant['id'],'expected_revision':1,'text':'changed'})
        self.t+=10001;verify=q.claim(self.store,'reader',clock=self.clock)
        self.assertEqual(verify['state'],'verifying');self.assertEqual(q.verify(self.store,verify,self.evidence(payload),clock=self.clock),'published')
    def test_wrong_identity_and_weak_lookup_do_not_allow_retry(self):
        row,job,payload=self.start();q.finish_send(self.store,job,{'outcome':'accepted','remote_id':'remote-test'},clock=self.clock)
        verify=q.claim(self.store,'reader',clock=self.clock)
        self.assertEqual(q.verify(self.store,verify,self.evidence(payload,identity_id='wrong'),clock=self.clock),'publication_unknown')
        self.assertEqual(reconcile.observe(self.store,row['id'],{'outcome':'not_found','strength':'recent_posts'},clock=self.clock),'publication_unknown')
        with self.assertRaises(ValueError):q.retry(self.store,row['id'],self.scope,self.iso(),clock=self.clock)
        self.assertEqual(reconcile.observe(self.store,row['id'],{'outcome':'not_found','strength':'authoritative','request_not_accepted':True,'submission_fence':job['fence'],'evidence_ref':'TEST contradictory log'},clock=self.clock),'publication_unknown')
    def test_proven_nonacceptance_after_unknown_allows_retry(self):
        row,job,_=self.start();q.finish_send(self.store,job,{'outcome':'unknown'},clock=self.clock)
        evidence={'outcome':'not_found','strength':'authoritative','request_not_accepted':True,'submission_fence':job['fence'],'evidence_ref':'TEST exact fake request log'}
        self.assertEqual(reconcile.observe(self.store,row['id'],evidence,clock=self.clock),'failed')
        self.assertEqual(q.retry(self.store,row['id'],self.scope,self.iso(),clock=self.clock)['state'],'queued')
    def test_cancel_before_send_and_unknown_cancel(self):
        row=self.enqueue();job=q.claim(self.store,'one',clock=self.clock);self.assertEqual(q.cancel(self.store,row['id'],clock=self.clock)['state'],'canceled')
        with self.assertRaises(ValueError):q.begin_send(self.store,job,clock=self.clock)
    def test_cancel_during_send_does_not_claim_remote_deletion(self):
        row,job,payload=self.start();self.assertEqual(q.cancel(self.store,row['id'],clock=self.clock)['state'],'cancel_requested')
        q.finish_send(self.store,job,{'outcome':'accepted','remote_id':'remote-test'},clock=self.clock)
        verify=q.claim(self.store,'reader',clock=self.clock);self.assertEqual(q.verify(self.store,verify,self.evidence(payload,moderation_pending=True),clock=self.clock),'awaiting_moderation')
        self.assertTrue(q.get(self.store,row['id'])['cancel_requested'])
    def test_expired_scope_changed_text_and_overdue_pause(self):
        row=self.enqueue(30,window=20);self.assertIsNone(q.claim(self.store,'one',clock=self.clock));self.t+=51
        self.assertIsNone(q.claim(self.store,'one',clock=self.clock));self.assertEqual(q.get(self.store,row['id'])['state'],'needs_review')
    def test_scope_checked_again_immediately_before_send(self):
        row=self.enqueue();job=q.claim(self.store,'one',clock=self.clock)
        with self.store.db() as c:c.execute('UPDATE content_scopes SET revoked=1')
        self.assertIsNone(q.begin_send(self.store,job,clock=self.clock));self.assertEqual(q.get(self.store,row['id'])['state'],'needs_review')
    def test_heartbeat_and_definite_failure_retry(self):
        row,job,_=self.start();self.t+=40;q.heartbeat(self.store,job,clock=self.clock);self.t+=30
        self.assertIsNone(q.claim(self.store,'other',clock=self.clock));q.finish_send(self.store,job,{'outcome':'not_sent','reason':'TEST rejection before send'},clock=self.clock)
        self.assertEqual(q.retry(self.store,row['id'],self.scope,self.iso(),clock=self.clock)['state'],'queued')

    def test_fake_worker_full_send_and_readback(self):
        outer=self
        class Fake:
            sends=0
            def preflight(self,payload):pass
            def publish(self,payload,request_key):self.sends+=1;return {'remote_id':'remote-test'}
            def read_back(self,payload,remote_id):return outer.evidence(payload)
        adapter=Fake();self.enqueue()
        self.assertEqual(worker.process_one(self.store,adapter,'worker',clock=self.clock)['state'],'verifying')
        self.assertEqual(worker.process_one(self.store,adapter,'worker',clock=self.clock)['state'],'published');self.assertEqual(adapter.sends,1)
        self.assertIsNone(worker.process_one(self.store,adapter,'worker',clock=self.clock))

    def test_worker_timeout_never_resends(self):
        class Fake:
            sends=0
            def preflight(self,payload):pass
            def publish(self,payload,request_key):self.sends+=1;raise TimeoutError('secret should not leak')
        adapter=Fake();self.enqueue();row=worker.process_one(self.store,adapter,'worker',clock=self.clock)
        self.assertEqual(row['state'],'publication_unknown');self.assertNotIn('secret',json.dumps(row))
        self.assertIsNone(worker.process_one(self.store,adapter,'worker',clock=self.clock));self.assertEqual(adapter.sends,1)

class ScheduleTests(unittest.TestCase):
    def test_dst_gap_and_ambiguity(self):
        with self.assertRaisesRegex(ValueError,'не существует'):schedule.from_local('2026-03-29T02:30:00','Europe/Berlin')
        with self.assertRaisesRegex(ValueError,'Неоднозначное'):schedule.from_local('2026-10-25T02:30:00','Europe/Berlin')
        first=schedule.from_local('2026-10-25T02:30:00','Europe/Berlin',0);second=schedule.from_local('2026-10-25T02:30:00','Europe/Berlin',1)
        self.assertEqual(schedule.instant(second,'Europe/Berlin')-schedule.instant(first,'Europe/Berlin'),3600)
        with self.assertRaises(ValueError):schedule.instant('2026-11-08T12:00:00','Europe/Moscow')

if __name__=='__main__':unittest.main()
