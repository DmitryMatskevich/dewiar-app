"""Private contact copies with synthetic native store; no personal address book or system prompt."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'core'),str(ROOT/'ui')]
from contacts_book import ContactBook,mention,norm

ROW={'source_id':'native-1','family_name':'Иванов','given_name':'Иван','middle_name':'Иванович','organization':'Тест',
     'phones':['+7 000 000-00-01'],'emails':['ivan@example.test'],'birthday':{'month':5,'day':15}}

class Native:
    def __init__(self):self.rows=[copy.deepcopy(ROW)];self.granted=True;self.limited=False;self.writes=[]
    def status(self):return dict(state='limited' if self.limited else 'authorized' if self.granted else 'denied',granted=self.granted,limited=self.limited)
    def fetch(self,query=None,identifiers=None):
        if not self.granted:raise PermissionError('denied')
        return copy.deepcopy([r for r in self.rows if identifiers is None or r['source_id'] in identifiers])
    def create(self,row):self.writes.append(copy.deepcopy(row));return 'created-native'

class ContactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'contacts.json';self.native=Native();self.book=ContactBook(self.path,self.native)
    def tearDown(self):self.tmp.cleanup()
    def preview(self):return self.book.prepare_import()
    def test_import_nameless_and_legacy_fields(self):
        self.native.rows=[dict(ROW,source_id='legacy',given_name='',family_name='',middle_name='',organization='',phones=['legacy extension'],emails=['legacy-address'])]
        p=self.preview();self.book.import_selected(p['token'],['legacy']);self.assertEqual(len(self.book.records()),1)
    def test_phone_search_ignores_formatting(self):
        p=self.preview();self.book.import_selected(p['token'],['native-1']);self.assertEqual(len(self.book.records('70000000001')),1)
    def test_bulk_import_and_repeat(self):
        self.native.rows=[dict(ROW,source_id=f'native-{i}',phones=[f'+700000{i:06d}']) for i in range(1000)]
        p=self.preview();ids=[r['source_id'] for r in p['contacts']]
        self.assertEqual(self.book.import_selected(p['token'],ids)['added'],1000)
        self.assertEqual(self.book.import_selected(p['token'],ids)['skipped'],1000)
        self.assertEqual(len(self.book.records()),1000)
    def test_different_native_identifiers_are_not_merged_on_basic_fields(self):
        self.native.rows.append(dict(ROW,source_id='native-other'))
        p=self.preview();self.book.import_selected(p['token'],['native-1','native-other'])
        self.assertEqual(len(self.book.records()),2)
    def test_partial_repeat_and_homonyms(self):
        self.native.rows.append(dict(ROW,source_id='native-2',phones=['+7 000 000-00-02']))
        p=self.preview();r=self.book.import_selected(p['token'],['native-1']);self.assertEqual(r['added'],1)
        self.assertEqual(self.book.import_selected(p['token'],['native-1'])['skipped'],1)
        self.assertEqual(self.book.import_selected(p['token'],['native-2'])['added'],1)
        self.assertEqual(len(self.book.records()),2);self.assertEqual(self.native.writes,[])
    def test_import_does_not_overwrite_unless_requested(self):
        p=self.preview();self.book.import_selected(p['token'],['native-1']);rid=self.book.records()[0]['id']
        self.book.data['records'][rid]['organization']='Моя правка';self.book.save()
        self.native.rows[0]['organization']='Новое из Mac';p=self.preview()
        self.book.import_selected(p['token'],['native-1']);self.assertEqual(self.book.records()[0]['organization'],'Моя правка')
        self.book.import_selected(p['token'],['native-1'],True);self.assertEqual(self.book.records()[0]['organization'],'Новое из Mac')
    def test_preview_token_scope_expiry_and_revocation(self):
        p=self.preview()
        for token,ids in [('bad',['native-1']),(p['token'],['other'])]:
            with self.assertRaises(ValueError):self.book.import_selected(token,ids)
        self.native.granted=False
        with self.assertRaises(PermissionError):self.book.import_selected(p['token'],['native-1'])
        self.native.granted=True;self.native.rows=[]
        with self.assertRaises(ValueError):self.book.import_selected(p['token'],['native-1'])
        self.assertFalse(self.book.records())
        self.book.preview['expires']=0
        with self.assertRaises(ValueError):self.book.import_selected(p['token'],['native-1'])
    def test_limited_access_does_not_delete_local_records(self):
        p=self.preview();self.book.import_selected(p['token'],['native-1']);self.native.rows=[];self.native.limited=True
        self.assertTrue(self.preview()['limited']);self.assertEqual(len(self.book.records()),1)
    def test_local_exact_duplicate_is_not_created_twice(self):
        a=self.book.create(ROW,False,'request-local-000001');b=self.book.create(ROW,False,'request-local-000002')
        self.assertEqual(a['id'],b['id']);self.assertTrue(b['already_exists']);self.assertEqual(len(self.book.records()),1)
    def test_creation_in_both_is_idempotent(self):
        row=dict(ROW,family_name='Сидоров');a=self.book.create(row,True,'request-000000001');b=self.book.create(row,True,'request-000000001')
        self.assertEqual(a,b);self.assertTrue(a['saved_mac']);self.assertEqual(len(self.native.writes),1);self.assertEqual(len(self.book.records()),1)
        self.assertEqual(os.stat(self.path).st_mode&0o777,0o600)
        loaded=ContactBook(self.path,self.native);self.assertEqual(loaded.create(row,True,'request-000000001'),a);self.assertEqual(len(self.native.writes),1)
    def test_duplicate_mac_card_and_no_permission_do_not_write(self):
        with self.assertRaises(ValueError):self.book.create(ROW,True,'request-000000002')
        self.native.granted=False
        with self.assertRaises(PermissionError):self.book.create(ROW,True,'request-000000003')
        self.assertFalse(self.native.writes);self.assertFalse(self.book.records())
        self.assertFalse(self.book.create(ROW,False,'request-000000004')['saved_mac'])
    def test_uncertain_native_write_is_not_retried(self):
        self.native.rows=[];self.native.create=Mock(side_effect=RuntimeError('uncertain'))
        for _ in range(2):
            with self.assertRaises(ValueError):self.book.create(ROW,True,'request-000000005')
        self.native.create.assert_called_once();self.assertFalse(self.book.records())
    def test_disk_failure_before_native_save_does_not_write(self):
        self.native.rows=[]
        with patch.object(self.book,'save',side_effect=OSError('disk full')):
            with self.assertRaises(ValueError):self.book.create(ROW,True,'request-native-disk')
        self.assertFalse(self.native.writes);self.assertNotIn('request-native-disk',self.book.data['operations'])
        self.assertTrue(self.book.create(ROW,True,'request-native-disk')['saved_mac'])
    def test_disk_failure_rolls_back_import_and_local_create(self):
        p=self.preview()
        with patch.object(self.book,'save',side_effect=OSError('disk full')):
            with self.assertRaises(ValueError):self.book.import_selected(p['token'],['native-1'])
            self.assertFalse(self.book.records())
            with self.assertRaises(ValueError):self.book.create(ROW,False,'request-000000006')
            self.assertFalse(self.book.records())
    def test_corrupt_file_not_silently_overwritten(self):
        self.path.write_text('{broken')
        with self.assertRaises(ValueError):ContactBook(self.path,self.native)
        self.assertEqual(self.path.read_text(),'{broken')
    def test_deferred_lookup_and_late_result_after_dismissal(self):
        self.native.fetch=Mock(wraps=self.native.fetch)
        p=self.book.proposal('Иванов Иван Иванович','chat:a',defer_native=True)
        self.assertTrue(p['searching']);self.native.fetch.assert_not_called()
        done=self.book.complete_lookup(p['id']);self.assertFalse(done['searching']);self.assertEqual(len(done['mac']),1)
        p=self.book.proposal('Иванов Иван Иванович','chat:b',defer_native=True);self.book.resolve_proposal(p['id'],'dismiss')
        self.assertIsNone(self.book.complete_lookup(p['id']))
    def test_no_automatic_write_or_reminder_on_mention(self):
        p=self.book.proposal('Обсуди встречу с Иванов Иван Иванович завтра','chat:a')
        self.assertEqual(len(p['mac']),1);self.assertFalse(self.path.exists());self.assertFalse(self.native.writes)
        self.assertNotIn('phones',json.dumps(p))
        self.assertIsNone(self.book.proposal('Иванов Иван Иванович','chat:a'))
        self.assertIsNotNone(self.book.proposal('Иванов Иван Иванович','chat:b'))
        self.book.resolve_proposal(p['id'],'dismiss');self.assertIsNone(self.book.proposal('Иванов Иван Иванович','chat:a'))
    def test_explicit_copy_and_ambiguous_names(self):
        self.native.rows.append(dict(ROW,source_id='native-2',phones=['+7 000 000-00-02']))
        p=self.book.proposal('Иванов Иван Иванович','chat:a');self.assertEqual(len(p['mac']),2)
        with self.assertRaises(ValueError):self.book.resolve_proposal(p['id'],'copy','other')
        self.assertEqual(self.book.resolve_proposal(p['id'],'copy','native-2')['added'],1)
        self.assertEqual(self.book.records()[0]['phones'],['+7 000 000-00-02']);self.assertFalse(self.native.writes)
    def test_surname_ich_and_command_not_part_of_name(self):
        self.assertEqual(mention('Спроси Дмитрий Мацкевич'),'Дмитрий Мацкевич')
        p=self.book.proposal('Дмитрий Иванович Мацкевич','chat:own')
        d=self.book.resolve_proposal(p['id'],'draft')['draft']
        self.assertEqual(d,dict(given_name='Дмитрий',middle_name='Иванович',family_name='Мацкевич'))
    def test_draft_name_order_and_unicode(self):
        for text in ['Иванов Иван Иванович','Иван Иванович Иванов']:
            p=self.book.proposal(text,'chat:'+text);draft=self.book.resolve_proposal(p['id'],'draft')['draft']
            self.assertEqual(draft,dict(family_name='Иванов',given_name='Иван',middle_name='Иванович'))
        self.assertEqual(norm('Семён'),norm('Семе\u0308н'))
        self.assertIsNone(mention('Составь план разработки приложения'))
    def test_deleting_local_copy_does_not_touch_mac(self):
        p=self.preview();self.book.import_selected(p['token'],['native-1']);rid=self.book.records()[0]['id'];self.book.delete(rid)
        self.assertEqual(len(self.native.rows),1);self.assertFalse(self.book.records())
    def test_permission_request_single_shot_and_timeout(self):
        import app
        from macshell import make_api
        with patch('mac_contacts.permission',return_value=dict(state='not_determined',granted=False,limited=False)),patch('PyObjCTools.AppHelper.callAfter') as dispatch,patch.object(sys,'frozen',True,create=True):
            api=make_api(app)('fixture')
            for _ in range(30):api.request_contacts_access()
            dispatch.assert_called_once();self.assertTrue(api.contacts_permission()['pending'])
            api._contacts_started-=61
            self.assertFalse(api.contacts_permission()['pending']);api.request_contacts_access();dispatch.assert_called_once()
    def test_native_card_bindings_without_saving(self):
        from mac_contacts import build_contact
        card=build_contact(dict(ROW,given_name='Тест'))
        self.assertEqual(str(card.givenName()),'Тест')
        self.assertEqual(str(card.phoneNumbers()[0].value().stringValue()),ROW['phones'][0])
        self.assertEqual(int(card.birthday().month()),5)
    def test_backup_contacts_only_in_closed_copy(self):
        import io,zipfile
        from backup import Backup
        root=Path(self.tmp.name);cfg=root/'cfg';state=root/'state';ws=root/'workspace'
        for folder in (cfg,state,ws):folder.mkdir()
        (state/'contacts.json').write_text('{"records":{}}')
        backup=Backup(cfg,state,ws)
        for password,present in [('',False),('test-only-password',True)]:
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:backup._fill(z,password=password)
            with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as z:self.assertEqual('state/contacts.json' in z.namelist(),present)
    def test_framework_status_is_read_only(self):
        import mac_contacts
        with patch('Contacts.CNContactStore') as store:
            store.authorizationStatusForEntityType_.return_value=4
            self.assertTrue(mac_contacts.permission()['limited']);store.alloc.assert_not_called()

if __name__=='__main__':unittest.main()
