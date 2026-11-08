"""Only temporary DB, encrypted test blobs and synthetic credentials."""
import concurrent.futures
import contextlib
import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore,SCHEMA_VERSION
from content import oauth_store as store
from content.oauth_vault import CredentialVault
from content.mcp_helper import CredentialBundle,LoginResult,HelperError


def result(token='FAKE-access',refresh='FAKE-refresh'):
    resource='https://mcp.higgsfield.ai/mcp';issuer='https://clerk.higgsfield.ai'
    value={'format':1,'resource':resource,'issuer':issuer,'client_info':{'client_id':'FAKE-client','issuer':issuer},
        'tokens':{'access_token':token,'refresh_token':refresh,'token_type':'Bearer','expires_in':3600,'scope':'openid offline_access'},
        'expires_at':time.time()+3600,'protected_metadata':{'resource':resource,'authorization_servers':[issuer]},
        'oauth_metadata':{'issuer':issuer,'authorization_endpoint':issuer+'/oauth/authorize','token_endpoint':issuer+'/oauth/token'}}
    return LoginResult({'state':'tools_discovered','resource':resource,'protocol':'2025-11-25','tools':[],'can_generate':False},CredentialBundle(value))


class OAuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.store=ContentStore(root/'workspace'/'Контент')
        self.vault=CredentialVault(root/'private-config'/'oauth',root/'private-config'/'vault.key')
        self.row=store.create(self.store,'ТЕСТ — Higgsfield','create')
    def begin(self,op='authorize',key=None):
        return store.begin(self.store,self.row['id'],store.get(self.store,self.row['id'])['revision'],op,key or uuid.uuid4().hex)
    def connect(self):
        job=self.begin();return store.finish(self.store,self.vault,self.row['id'],job['intent_id'],result())
    def private(self):
        with self.store.db() as c:return store._get(c,self.row['id'])

    def test_crud_idempotency_conflict_archive_restore(self):
        self.assertEqual(store.create(self.store,'ТЕСТ — Higgsfield','create'),self.row)
        changed=store.update(self.store,self.row['id'],1,label='ТЕСТ — компания')
        with self.assertRaisesRegex(ValueError,'revision_conflict'):store.update(self.store,self.row['id'],1,label='late')
        archived=store.update(self.store,self.row['id'],changed['revision'],archived=True)
        self.assertEqual(store.listing(self.store),[])
        with self.assertRaisesRegex(ValueError,'архиве'):self.begin()
        store.update(self.store,self.row['id'],archived['revision'],archived=False)
        self.assertEqual(len(store.listing(self.store)),1)

    def test_encrypted_binding_and_public_data_have_no_credentials(self):
        connected=self.connect();row=self.private();ref=row['credential_ref'];bound=store.binding(self.store,row)
        self.assertNotIn('FAKE-access',repr(connected));self.assertNotIn('FAKE-refresh',repr(store.history(self.store,row['id'])))
        self.assertNotIn(b'FAKE-access',self.store.path.read_bytes())
        blob=self.vault._path(ref);self.assertNotIn(b'FAKE-access',blob.read_bytes())
        if os.name!='nt':self.assertEqual(blob.stat().st_mode&0o777,0o600)
        self.assertEqual(self.vault.get(ref,bound).value['tokens']['access_token'],'FAKE-access')
        for wrong in [{**bound,'connection':'other'},{**bound,'generation':999},{**bound,'workspace':'other'}]:
            with self.assertRaises(HelperError):self.vault.get(ref,wrong)

    def test_refresh_serializes_two_windows_and_same_key_is_idempotent(self):
        row=self.connect()
        def begin(key):
            try:return store.begin(self.store,row['id'],row['revision'],'refresh',key)
            except ValueError:return 'conflict'
        with concurrent.futures.ThreadPoolExecutor(2) as pool:answers=list(pool.map(begin,['one','two']))
        self.assertEqual(sum(isinstance(x,dict) for x in answers),1)
        job=next(x for x in answers if isinstance(x,dict));winning='one' if isinstance(answers[0],dict) else 'two'
        self.assertEqual(begin(winning)['intent_id'],job['intent_id']);self.assertFalse(begin(winning)['created'])
        self.assertEqual(store.credential(self.store,self.vault,row['id'],job['intent_id']).value['tokens']['refresh_token'],'FAKE-refresh')

    def test_rotation_replaces_exact_ref_and_forgets_prior_blob(self):
        self.connect();old=self.private()['credential_ref'];job=self.begin('refresh')
        store.finish(self.store,self.vault,self.row['id'],job['intent_id'],result('FAKE-new','FAKE-rotated'))
        row=self.private();self.assertNotEqual(row['credential_ref'],old);self.assertEqual(row['credential_generation'],2)
        self.assertFalse(self.vault._path(old).exists())
        self.assertEqual(self.vault.get(row['credential_ref'],store.binding(self.store,row)).value['tokens']['refresh_token'],'FAKE-rotated')

    def test_disconnect_fences_late_login_without_orphan(self):
        job=self.begin();row=store.get(self.store,self.row['id'])
        store.disconnect(self.store,self.vault,row['id'],row['revision'])
        with self.assertRaisesRegex(HelperError,'superseded'):store.finish(self.store,self.vault,row['id'],job['intent_id'],result())
        self.assertFalse(self.vault.root.exists());self.assertEqual(store.get(self.store,row['id'])['state'],'disconnected')

    def test_ambiguous_refresh_or_deadline_requires_new_login(self):
        self.connect();job=self.begin('refresh');store.fail(self.store,self.row['id'],job['intent_id'],'timeout')
        self.assertEqual(store.get(self.store,self.row['id'])['state'],'needs_login')
        with self.assertRaisesRegex(ValueError,'заново'):self.begin('refresh')
        job=self.begin();
        with self.store.db() as c:c.execute('UPDATE content_oauth_connections SET deadline=1 WHERE id=?',(self.row['id'],))
        self.assertEqual(store.get(self.store,self.row['id'])['last_error'],'interrupted')
        with self.assertRaisesRegex(HelperError,'superseded'):store.finish(self.store,self.vault,self.row['id'],job['intent_id'],result())

    def test_vault_or_db_failure_never_connects_and_cleans_new_blob(self):
        job=self.begin()
        with patch.object(self.vault,'put',side_effect=HelperError('credential_unavailable')):
            with self.assertRaises(HelperError):store.finish(self.store,self.vault,self.row['id'],job['intent_id'],result())
        self.assertEqual(self.private()['state'],'authenticating')
        original=self.store.db
        @contextlib.contextmanager
        def failure():
            with original() as c:
                class Proxy:
                    def execute(self,sql,*args):
                        if "SET revision=revision+1,state='connected'" in sql:raise RuntimeError('injected failure')
                        return c.execute(sql,*args)
                yield Proxy()
        with patch.object(self.store,'db',failure):
            with self.assertRaises(RuntimeError):store.finish(self.store,self.vault,self.row['id'],job['intent_id'],result())
        self.assertEqual(self.private()['state'],'authenticating');self.assertEqual(list(self.vault.root.glob('*.bin')),[])

    def test_corrupt_missing_blob_and_foreign_result_fail_closed(self):
        self.connect();job=self.begin('refresh');ref=self.private()['credential_ref'];self.vault._path(ref).write_bytes(b'corrupt')
        with self.assertRaisesRegex(HelperError,'credential_unavailable'):store.credential(self.store,self.vault,self.row['id'],job['intent_id'])
        bad=result();bad.credential.value['issuer']='https://wrong.example'
        with self.assertRaises(HelperError):store.finish(self.store,self.vault,self.row['id'],job['intent_id'],bad)
        self.assertEqual(self.private()['state'],'verifying')

    def test_schema19_upgrade_preserves_material_bytes(self):
        row=self.store.create({'title':'Legacy'},'legacy')
        with self.store.db() as c:
            before=c.execute('SELECT snapshot FROM revisions WHERE material_id=?',(row['id'],)).fetchone()[0]
            c.execute('DROP TABLE content_video_revision_sources')
            c.execute('DROP TABLE content_oauth_events');c.execute('DROP TABLE content_oauth_connections');c.execute('PRAGMA user_version=19')
        again=ContentStore(self.store.root)
        with again.db() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
            self.assertEqual(c.execute('SELECT snapshot FROM revisions WHERE material_id=?',(row['id'],)).fetchone()[0],before)
        self.assertTrue(list((self.store.root/'backups').glob(f'before-v19-to-v{SCHEMA_VERSION}-*.db')))


if __name__=='__main__':unittest.main()
