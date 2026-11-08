"""Editorial evidence and readiness are bound to an exact revision."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore
from content import review

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(self.tmp.name)
        self.row=self.store.create({'title':'Обслуживание ЦОД','author':'Автор','body':'Проверка длится 30 минут.'},'m',source={'sha256':'a'*64,'text':'Обслуживание ЦОД. Проверка длится 30 минут.','task_title':'Протокол'})
    def args(self,**extra):
        items=[{'block_id':b['id'],'status':'supported','quote':b['text'],'source_sha256':'a'*64,'url':'','note':''} for b in review.review_blocks(self.row)]
        return {'id':self.row['id'],'expected_revision':1,'expected_review_version':0,'reviewer':'Редактор','decisions':items,'ready':True,**extra}
    def test_ready_invalidated_on_material_change_restore_not_reapprove(self):
        result=review.save(self.store,self.args());self.assertTrue(result['ready'])
        self.store.update(self.row['id'],1,{'title':'Правка'});self.assertFalse(review.report(self.store,self.row['id'])['ready'])
        self.store.update(self.row['id'],2,{},restore=1);self.assertFalse(review.report(self.store,self.row['id'])['ready'])
    def test_quote_missing_numbers_and_false_quote_rejected(self):
        for quote in ['Проверка длится неизвестно сколько','Новая выдуманная цитата 30 минут']:
            args=self.args();args['decisions'][1]['quote']=quote
            with self.assertRaises(ValueError):review.save(self.store,args)
        args=self.args();args['decisions'][1].update(quote='Проверка оборудования',url='https://example.com/document')
        with self.assertRaisesRegex(ValueError,'отсутствуют числа'):review.save(self.store,args)
    def test_external_evidence_explicitly_editor_declared(self):
        args=self.args();args['decisions'][1].update(quote='По протоколу проверка составляет 30 минут',url='https://example.com/protocol')
        result=review.save(self.store,args);self.assertEqual(result['claims'][1]['evidence_kind'],'editor_declared_external')
    def test_pending_disputed_and_missing_author_block_ready(self):
        args=self.args();args['decisions'][1]['status']='disputed';args['ready']=False
        result=review.save(self.store,args);self.assertFalse(result['ready'])
        args.update(expected_review_version=1,ready=True)
        with self.assertRaisesRegex(ValueError,'не готов'):review.save(self.store,args)
    def test_review_conflict_and_title_required(self):
        review.save(self.store,self.args())
        with self.assertRaisesRegex(ValueError,'review_conflict'):review.save(self.store,self.args())
        args=self.args(expected_review_version=1);args['decisions']=args['decisions'][1:]
        with self.assertRaisesRegex(ValueError,'заголовка'):review.save(self.store,args)
    def test_opinion_cannot_bypass_numeric_check(self):
        args=self.args();args['decisions'][1].update(status='opinion',note='Личное авторское суждение')
        with self.assertRaisesRegex(ValueError,'с числами'):review.save(self.store,args)
    def test_stale_material_and_unsafe_url_rejected(self):
        args=self.args();args['decisions'][1]['url']='javascript:alert(1)'
        with self.assertRaises(ValueError):review.save(self.store,args)
        self.store.update(self.row['id'],1,{'author':'Другой автор'})
        with self.assertRaisesRegex(ValueError,'revision_conflict'):review.save(self.store,self.args())

if __name__=='__main__':unittest.main()
