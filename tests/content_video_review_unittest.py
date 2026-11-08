"""QA decisions on temp fake media receipts; no real publishing or user data."""
import concurrent.futures
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import content_captions_unittest as fixtures
from content import captions,video_review as qa,video_store


class ReviewTests(unittest.TestCase):
    # Reuse only isolated setup/cleanup, not the source test methods.
    setUp=fixtures.CaptionTests.setUp
    def review_args(self,**patch):
        return {'import_id':'fake-import','expected_revision':0,'video_revision':1,'reviewer':'TEST editor','note':'Synthetic mechanism check',
                'checks':{key:True for key in qa.CHECKS},'caption_language':None,'caption_revision':None,'decision':'accepted','request_key':'qa',**patch}

    def test_accept_is_editor_declared_and_not_publication_permission(self):
        row=qa.save(self.store,self.review_args());self.assertEqual(qa.save(self.store,self.review_args()),row)
        report=qa.get(self.store,'fake-import');self.assertTrue(report['accepted']);self.assertFalse(report['publication_authorized']);self.assertEqual(report['evidence_kind'],'editor_declared')
        with self.store.db() as c:self.assertIsNone(json.loads(c.execute('SELECT receipt FROM content_video_imports').fetchone()[0]).get('accepted'))

    def test_all_checks_required_and_tampered_file_rejected(self):
        with self.assertRaises(ValueError):qa.save(self.store,self.review_args(checks={key:False for key in qa.CHECKS}))
        self.asset.write_bytes(b'Changed asset')
        with self.assertRaisesRegex(ValueError,'Контрольная сумма'):qa.save(self.store,self.review_args())

    def test_caption_changes_invalidate_acceptance_and_withdraw_still_works(self):
        captions.save(self.store,self.args)
        args=self.review_args(caption_language='ru',caption_revision=1)
        qa.save(self.store,args);self.assertTrue(qa.get(self.store,'fake-import')['accepted'])
        captions.archive(self.store,{'import_id':'fake-import','language':'ru','expected_revision':1,'archived':True})
        self.assertFalse(qa.get(self.store,'fake-import')['accepted'])
        withdrawn=qa.save(self.store,{**args,'expected_revision':1,'decision':'withdrawn','request_key':'withdraw','note':'Changed captions'})
        self.assertEqual(withdrawn['caption_ref']['revision'],1)
        self.assertEqual(withdrawn['decision'],'withdrawn')

    def test_stale_script_and_archive_cannot_be_accepted(self):
        with self.store.db() as c:ident=c.execute('SELECT video_id FROM content_video_imports').fetchone()[0]
        video_store.update(self.store,{'id':ident,'expected_revision':1,'patch':{'title':'Changed source'}})
        with self.assertRaisesRegex(ValueError,'устаревшего'):qa.save(self.store,self.review_args())
        draft=qa.save(self.store,self.review_args(decision='draft'))
        self.assertEqual(draft['decision'],'draft');self.assertFalse(qa.get(self.store,'fake-import')['accepted'])

    def test_concurrent_review_only_one_wins_and_history_survives(self):
        def save(key):
            try:return qa.save(self.store,self.review_args(request_key=key))['revision']
            except ValueError as e:return str(e).split(':')[0]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(save,['one','two']))
        self.assertEqual(results.count(1),1);self.assertEqual(results.count('revision_conflict'),1)
        qa.save(self.store,self.review_args(expected_revision=1,decision='rejected',request_key='reject',note='TEST correction'))
        history=qa.dispatch(self.store,'content_video_review_history',{'import_id':'fake-import'})
        self.assertEqual([r['decision'] for r in history],['rejected','accepted'])

    def test_changed_file_after_acceptance_and_wrong_refs(self):
        qa.save(self.store,self.review_args());self.asset.unlink();self.assertFalse(qa.get(self.store,'fake-import')['accepted'])
        with self.assertRaises(ValueError):qa.save(self.store,self.review_args(expected_revision=1,decision='draft',video_revision=2,request_key='wrong'))
        with self.assertRaises(ValueError):qa.dispatch(self.store,'content_video_review_save',{**self.review_args(),'publication_authorized':True})


if __name__=='__main__':unittest.main()
