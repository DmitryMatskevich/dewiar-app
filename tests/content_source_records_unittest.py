"""Revision-bound multi-source contracts, all fixtures local and isolated."""
import base64
import concurrent.futures
import hashlib
import io
import json
import sqlite3
import sys
import unittest
import uuid
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import content_sources_unittest as fixtures
from content import source_records as sr,review,generation
from content.commands import dispatch
from content.store import ContentStore,dump,SCHEMA_VERSION


class SourceRecordTests(unittest.TestCase):
    def setUp(self):
        fixtures.SourcesTests.setUp(self)
        self.row=self.store.create({'title':'Мнение автора','author':'Автор','body':'Субъективный взгляд'},'article')
    def change(self,operation='add',**patch):
        current=self.store.get(self.row['id'])
        return sr.change(self.store,{'id':self.row['id'],'expected_revision':current['revision'],'operation':operation,'request_key':uuid.uuid4().hex,**patch},self.tasks)
    def refs(self,revision=None):return sr.listing(self.store,{'id':self.row['id'],**({'revision':revision} if revision else {})})
    def package(self,revision):return dispatch(self.store,'content_export',{'id':self.row['id'],'revision':revision})
    def add(self,title='Источник',text='Одинаковый текст источника'):
        return self.change(note={'title':title,'text':text})

    def test_history_export_is_byte_stable_after_replacement_and_removal(self):
        row=self.add();old=self.package(row['revision']);ident=self.refs()[0]['source_id']
        self.change('replace',source_id=ident,note={'title':'Другой','text':'Изменённый текст'})
        self.change('remove',source_id=self.refs()[0]['source_id'])
        self.assertEqual(self.package(row['revision']),old);self.assertEqual(self.store.sources(row['id']),[])
        self.assertEqual(self.store.sources(row['id'],row['revision'])[0]['task_title'],'Источник')
        diff=dispatch(self.store,'content_diff',{'id':row['id'],'from_revision':2,'to_revision':4})
        self.assertTrue(diff['sources']['changed']);self.assertEqual(diff['sources']['after'],[])

    def test_reorder_restore_and_noop_preserve_source_history(self):
        self.add('Первый');self.add('Второй');original=[r['source_id'] for r in self.refs()]
        changed=self.change('reorder',source_ids=original[::-1]);self.assertEqual(changed['revision'],4)
        same=self.change('reorder',source_ids=original[::-1]);self.assertEqual(same,changed)
        restored=self.store.update(self.row['id'],4,{},restore=3)
        self.assertEqual(restored['revision'],5);self.assertEqual([r['source_id'] for r in self.refs()],original)
        self.assertEqual([r['source_id'] for r in self.refs(4)],original[::-1])

    def test_retry_does_not_reread_deleted_task_result(self):
        args={'id':self.row['id'],'expected_revision':1,'operation':'add','task_id':'task-id','path':'outputs/transcript.md','request_key':'attach'}
        first=sr.change(self.store,args,self.tasks);self.file.unlink()
        self.assertEqual(sr.change(self.store,args,self.tasks),first)
        with self.assertRaisesRegex(ValueError,'request_conflict'):sr.change(self.store,{**args,'path':'outputs/other.md'},self.tasks)
        self.assertEqual(len(self.refs()),1)

    def test_copy_requested_version_and_roundtrip_keep_both_origins(self):
        self.add('Докладчик А');row=self.add('Докладчик Б');bundle=self.package(row['revision'])
        self.assertEqual(len({r['source_id'] for r in self.refs()}),2);self.assertEqual(len({r['sha256'] for r in self.refs()}),1)
        self.change('remove',source_id=self.refs()[0]['source_id'])
        copy=dispatch(self.store,'content_copy',{'id':row['id'],'source_revision':row['revision'],'data':{'title':'Копия'},'request_key':'copy'})
        self.assertEqual(len(self.store.sources(copy['id'])),2)
        target=ContentStore(Path(self.tmp.name)/'target')
        imported=dispatch(target,'content_import_package',{'b64':bundle['b64'],'request_key':'import'})
        sources=target.sources(imported['id']);self.assertEqual([s['task_title'] for s in sources],['Докладчик А','Докладчик Б'])
        self.assertTrue(all(s['attribution']=='package_claimed' for s in sources))

    def test_competing_changes_roll_back_losing_source_record(self):
        def add(n):
            try:return self.change(expected_revision=1,note={'title':n,'text':'Текст'})['revision']
            except ValueError as e:return str(e).split(':')[0]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(add,['one','two']))
        self.assertCountEqual(results,[2,'revision_conflict'])
        with self.store.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM content_source_records').fetchone()[0],1)

    def test_limits_duplicate_and_invalid_order_leave_revision_unchanged(self):
        for i in range(20):self.add(str(i))
        for note in [{'title':'extra','text':'текст'},{'title':'0','text':'Одинаковый текст источника'},{'title':'oversize','text':'я'*600000},{'title':'nul','text':'a\x00b'}]:
            with self.assertRaises(ValueError):self.change(note=note)
        with self.assertRaises(ValueError):self.change('reorder',source_ids=['missing'])
        with self.assertRaises(ValueError):self.change('remove',source_id='missing')
        self.assertEqual(self.store.get(self.row['id'])['revision'],21)
        with self.store.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM content_source_records').fetchone()[0],20)

    def test_exact_source_selection_resolves_identical_hash_origins(self):
        self.add('A');self.add('B');sources=review.source_texts(self.store,self.row['id'])
        items=[{'block_id':b['id'],'status':'supported','quote':'Одинаковый текст источника','source_sha256':sources[0]['sha256'],'url':'','note':''} for b in review.review_blocks(self.row)]
        with self.assertRaisesRegex(ValueError,'точный источник'):review.decisions(self.row,items,sources)
        exact=review.decisions(self.row,[{**x,'source_id':sources[1]['source_id']} for x in items],sources)
        self.assertTrue(all(x['source_id']==sources[1]['source_id'] for x in exact))

    def test_source_change_resets_ready_for_new_revision(self):
        items=[{'block_id':b['id'],'status':'opinion','quote':'','source_sha256':'','url':'','note':'Авторское суждение проверено редактором'} for b in review.review_blocks(self.row)]
        review.save(self.store,{'id':self.row['id'],'expected_revision':1,'expected_review_version':0,'reviewer':'Автор','ready':True,'decisions':items})
        self.assertTrue(review.report(self.store,self.row['id'])['ready']);self.add()
        self.assertFalse(review.report(self.store,self.row['id'])['ready'])
        with self.store.db() as c:self.assertTrue(json.loads(c.execute('SELECT snapshot FROM editorial_reviews WHERE revision=1').fetchone()[0])['ready'])

    def test_generation_keeps_exact_sources_and_rejects_stale_apply(self):
        row=self.add();selected=row['blocks'][0]['id'];seen=[]
        def run(system,prompt,cancel):
            seen.append(json.loads(prompt));yield json.dumps({'replacements':[{'id':selected,'text':'TEST rewrite'}],'issues':[]})
        jid=generation.start(self.store,{'id':row['id'],'expected_revision':2,'brief':'TEST','block_ids':[selected],'request_key':'gen'},({'id':'fake','test_only':True},run))['id']
        with generation._lock:active=generation._active.get((str(self.store.path),jid))
        if active:active[1].join(3);self.assertFalse(active[1].is_alive())
        self.add('New');self.assertEqual(len(seen[0]['sources']),1)
        with self.assertRaisesRegex(ValueError,'revision_conflict'):generation.apply(self.store,jid)

    def test_migration_preserves_legacy_json_and_attaches_all_old_versions(self):
        legacy={'sha256':'a'*64,'text':'Текст прежнего источника','task_title':'Legacy'}
        other=self.store.create({'title':'Legacy'},'legacy',source=legacy);self.store.update(other['id'],1,{'title':'Legacy 2'})
        with self.store.db() as c:
            before=[tuple(r) for r in c.execute('SELECT * FROM revisions')]
            c.execute('DROP TABLE content_video_revision_sources')
            c.execute('DROP TABLE content_oauth_events');c.execute('DROP TABLE content_oauth_connections')
            c.execute('DROP TABLE content_revision_sources');c.execute('DROP TABLE content_source_records');c.execute('PRAGMA user_version=18')
        reopened=ContentStore(self.store.root)
        self.assertEqual(reopened.sources(other['id'],1),[legacy]);self.assertEqual(reopened.sources(other['id'],2),[legacy])
        with reopened.db() as c:
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM revisions')],before)
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)

    def test_archived_material_and_foreign_source_do_not_change(self):
        self.add();self.store.update(self.row['id'],2,{},archived=True)
        with self.assertRaisesRegex(ValueError,'архив'):self.add('Other')
        with self.assertRaises(ValueError):sr.dispatch(self.store,'content_source_record_text',{'id':self.row['id'],'source_id':'missing','revision':2})


if __name__=='__main__':unittest.main()
