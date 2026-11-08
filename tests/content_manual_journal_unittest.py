"""Bounded manual publication journal; isolated workspace and no remote writes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore,dump
from content import social_store as social,distribution as dist,manual,packages


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(self.tmp.name)
        article=self.store.create({'title':'TEST journal','body':'Original body'},'article')
        def add(kind,data):return social.save(self.store,{'kind':kind,'data':data,'request_key':uuid.uuid4().hex})
        conn=add('connections',{'name':'Test LinkedIn'})
        identity=add('identities',{'name':'Original author','url':'https://linkedin.com/in/test'})
        dest=add('destinations',{'name':'Original destination','url':'https://linkedin.com/in/test'})
        binding=add('bindings',{'name':'Test route','connection_id':conn['id'],'identity_id':identity['id'],'destination_id':dest['id']})
        self.campaign=dist.create(self.store,{'id':article['id'],'source_revision':1,'binding_ids':[binding['id']],'request_key':'campaign'})
        self.variant=self.campaign['variants'][0]
        self.package=packages.prepare(self.store,{'variant_id':self.variant['id'],'expected_revision':1})
    def report(self,n):return manual.report(self.store,{'package_hash':self.package['hash'],'permalink':'https://linkedin.com/feed/update/test-'+str(n),'note':'TEST '+str(n)})
    def page(self,cursor=None,limit=10):return dist.dispatch(self.store,'content_manual_receipts',{'variant_id':self.variant['id'],'cursor':cursor,'limit':limit})
    def test_three_pages_freeze_top_boundary_and_no_duplicates_with_concurrent_insert(self):
        records=[self.report(i) for i in range(23)]
        first=self.page();self.assertEqual(first['total'],23);self.assertEqual([r['id'] for r in first['items']],[r['id'] for r in records[-1:-11:-1]])
        new=self.report('new');second=self.page(first['next_cursor']);third=self.page(second['next_cursor'])
        ids=[r['id'] for p in [first,second,third] for r in p['items']]
        self.assertEqual(ids,[r['id'] for r in reversed(records)]);self.assertEqual(len(set(ids)),23)
        self.assertIsNone(third['next_cursor']);self.assertEqual(third['total'],23)
        self.assertEqual(self.page(first['cursor']),first)
        refreshed=self.page();self.assertEqual(refreshed['total'],24);self.assertEqual(refreshed['items'][0]['id'],new['id'])
    def test_limits_empty_unknown_and_foreign_cursors(self):
        self.assertEqual(self.page(),{'items':[],'total':0,'cursor':None,'next_cursor':None})
        report=self.report(1)
        for limit in [0,51,True,'10']:
            with self.subTest(limit=limit),self.assertRaises(ValueError):self.page(limit=limit)
        for cursor in [{},{'anchor':'unknown','before':None},{'anchor':report['id'],'before':False},{'anchor':report['id'],'before':'unknown'}]:
            with self.subTest(cursor=cursor),self.assertRaises(ValueError):self.page(cursor)
        other=dist.create(self.store,{'id':self.campaign['source']['id'],'source_revision':1,'binding_ids':[self.variant['target']['binding']['id']],'request_key':'other'})
        with self.assertRaisesRegex(ValueError,'не относится'):manual.page(self.store,{'variant_id':other['variants'][0]['id'],'cursor':self.page()['cursor']})
        later=self.report(2)
        with self.assertRaisesRegex(ValueError,'границы'):self.page({'anchor':report['id'],'before':later['id']})
    def test_exact_package_survives_variant_change_archive_and_withdraw(self):
        record=self.report(1)
        dist.update(self.store,{'variant_id':self.variant['id'],'expected_revision':1,'text':'New text'})
        dist.archive(self.store,{'campaign_id':self.campaign['id'],'expected_revision':1,'archived':True})
        exact=dist.dispatch(self.store,'content_manual_receipt_get',{'receipt_id':record['id']})
        self.assertEqual(exact['payload'],self.package['payload']);self.assertEqual(exact['payload']['target']['identity']['name'],'Original author')
        manual.withdraw(self.store,{'receipt_id':record['id'],'reason':'Correction'})
        self.assertEqual(self.page()['items'][0]['state'],'withdrawn')
        changed=manual.receipt_get(self.store,{'receipt_id':record['id']});self.assertEqual(changed['receipt']['withdrawn_reason'],'Correction')
        self.assertEqual(changed['payload'],exact['payload']);self.assertNotIn('credential',dump(changed))
        with self.assertRaises(ValueError):manual.receipt_get(self.store,{'receipt_id':'unknown'})
    def test_compact_campaign_stays_capped_and_journal_never_embeds_packages(self):
        for i in range(55):self.report(i)
        compact=dist.get(self.store,self.campaign['id']);self.assertEqual(len(compact['variants'][0]['receipts']),10)
        page=self.page(limit=50);self.assertEqual(len(page['items']),50);self.assertEqual(page['total'],55)
        self.assertNotIn('payload',dump(page));self.assertNotIn('Original body',dump(page))
        self.assertLess(len(dump(page)),30000)
    def test_same_date_order_reopen_and_old_withdrawal_remain_visible(self):
        rows=[self.report(i) for i in range(12)]
        with self.store.db() as c:c.execute("UPDATE content_manual_receipts SET created_at='2026-11-08T00:00:00Z'")
        first=self.page();self.store=ContentStore(self.tmp.name)
        manual.withdraw(self.store,{'receipt_id':rows[0]['id'],'reason':'Old error'})
        tail=self.page(first['next_cursor']);self.assertEqual([r['id'] for r in tail['items']],[rows[1]['id'],rows[0]['id']])
        self.assertEqual(tail['items'][1]['state'],'withdrawn');self.assertEqual(tail['total'],12)
    def test_tampered_package_is_not_presented_as_original(self):
        record=self.report(1)
        with self.store.db() as c:
            payload={**self.package['payload'],'text':'Tampered'}
            c.execute('UPDATE content_packages SET snapshot=? WHERE hash=?',(json.dumps(payload),self.package['hash']))
        with self.assertRaisesRegex(ValueError,'повреждён'):manual.receipt_get(self.store,{'receipt_id':record['id']})

if __name__=='__main__':unittest.main()
