"""Temporary-only backups and copied workspaces, never Engine/settings/network."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from backup import Backup
from content.store import ContentStore
from content import oauth_store
from content.oauth_service import Service,command
from content_oauth_store_unittest import result
from interface_origin import authenticated_interface
from types import SimpleNamespace


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.cfg=self.root/'config';self.ws=self.root/'workspace';self.state=self.cfg/'state'
        self.state.mkdir(parents=True);self.ws.mkdir()
        (self.cfg/'settings.json').write_text('{}')
        self.backup=Backup(self.cfg,self.state,self.ws)

    def test_plain_and_encrypted_backups_exclude_oauth_and_runtimes(self):
        for name in ['content-oauth-vault/test.bin','runtimes/content-mcp/2.3.0/bin/python']:
            path=self.cfg/name;path.parent.mkdir(parents=True);path.write_bytes(b'LOCAL-ONLY')
        for password in ['', 'test-password']:
            made=self.backup.make(self.root/'backups',password=password)
            _,archive=self.backup.open(made['path'],password)
            with archive:
                self.assertIn('config/settings.json',archive.namelist())
                self.assertFalse(any('content-oauth-vault' in n or 'runtimes' in n for n in archive.namelist()))
                self.assertNotIn(b'LOCAL-ONLY',b''.join(archive.read(n) for n in archive.namelist()))

    def test_both_restore_modes_ignore_local_only_even_in_old_archive(self):
        path=self.root/'old.zip'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('manifest.json',json.dumps({'app':'Dewiar','time':'2026-11-08T00:00:00','host':self.backup.host}))
            for name in ['content-oauth-vault/test.bin','./runtimes/content-mcp/2.3.0/bin/python','runtimes\\foreign\\file','./vault.key']:
                z.writestr('config/'+name,b'IMPORTED-LOCAL')
            z.writestr('workspace/Note.md','ordinary note')
        for mode in ['replace','merge']:
            with self.subTest(mode=mode):
                keep=self.cfg/'content-oauth-vault/test.bin';keep.parent.mkdir(exist_ok=True);keep.write_bytes(b'original')
                self.backup.restore(path,mode=mode)
                self.assertEqual(keep.read_bytes(),b'original')
                self.assertFalse((self.cfg/'runtimes').exists());self.assertFalse((self.cfg/'vault.key').exists())
                self.assertEqual((self.ws/'Note.md').read_text(),'ordinary note')


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=ContentStore(self.root/'source/Контент');self.service=Service(self.root/'config',self.root/'config/key')
        self.addCleanup(self.service.close)
        self.row=oauth_store.create(self.store,'ТЕСТ — original','create')
        job=oauth_store.begin(self.store,self.row['id'],1,'authorize','authorize')
        self.row=oauth_store.finish(self.store,self.service.vault,self.row['id'],job['intent_id'],result())

    def copy(self):
        copied=ContentStore(self.root/'copied/Контент')
        with self.store.db() as source, copied.db() as target:source.backup(target)
        return copied

    def test_missing_credential_get_and_list_require_login_without_deleting_card(self):
        for blob in self.service.vault.root.glob('*.bin'):blob.unlink()
        with authenticated_interface():
            rows=command(SimpleNamespace(_content_oauth_service=self.service),self.store,'content_oauth_list',{})
        self.assertEqual(rows[0]['state'],'needs_login');self.assertEqual(rows[0]['label'],self.row['label'])
        value=self.service.get(self.store,self.row['id']);self.assertEqual(value['last_error'],'credential_unavailable')
        self.assertEqual(value['revision'],self.row['revision']+1)
        self.assertEqual(len([e for e in value['events'] if e['event']=='credential_unavailable']),1)

    def test_copy_disconnect_cannot_delete_original_blob(self):
        copied=self.copy();original=list(self.service.vault.root.glob('*.bin'))
        value=self.service.get(copied,self.row['id']);self.assertEqual(value['state'],'needs_login')
        self.service.disconnect(copied,value['id'],value['revision'])
        self.assertEqual(list(self.service.vault.root.glob('*.bin')),original)
        self.assertEqual(self.service.get(self.store,self.row['id'])['state'],'connected')

    def test_copy_new_login_keeps_original_blob_and_binds_new_generation(self):
        copied=self.copy();value=self.service.get(copied,self.row['id'])
        job=oauth_store.begin(copied,value['id'],value['revision'],'authorize','copied-login')
        value=oauth_store.finish(copied,self.service.vault,value['id'],job['intent_id'],result('OTHER-access','OTHER-refresh'))
        self.assertEqual(value['state'],'connected');self.assertEqual(len(list(self.service.vault.root.glob('*.bin'))),2)
        self.assertEqual(self.service.get(self.store,value['id'])['state'],'connected')
        self.assertEqual(self.service.get(copied,value['id'])['state'],'connected')


class FrozenResourcesTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('CONTENT_MCP_TEST_PYTHON'),'optional SDK runtime required')
    def test_bundled_helper_works_readonly_without_sdk_in_main_process(self):
        # Evaluate only data declarations; no packaging, signing or installed .app use.
        tree=ast.parse((ROOT/'packaging/macos.spec').read_text());scope={'root':ROOT}
        nodes=[n for n in tree.body if (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='datas' for t in n.targets)) or (isinstance(n,ast.AugAssign) and isinstance(n.target,ast.Name) and n.target.id=='datas')]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<bundle-data>','exec'),scope)
        resources=[(Path(src),dest) for src,dest in scope['datas'] if 'content_mcp_runtime' in src or 'requirements-content-mcp' in src]
        self.assertEqual(len(resources),2)
        with tempfile.TemporaryDirectory() as folder:
            bundle=Path(folder)/'bundle';bundle.mkdir()
            for src,dest in resources:
                if src.is_dir():shutil.copytree(src,bundle/dest,ignore=shutil.ignore_patterns('__pycache__'))
                else:shutil.copyfile(src,bundle/dest/src.name)
            files=list(bundle.rglob('*'))
            hashes={p.relative_to(bundle).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
            script='import sys;from pathlib import Path;sys._MEIPASS='+repr(str(bundle))+';sys.path.insert(0,'+repr(str(ROOT/'core'))+');from content.mcp_helper import call,ENTRY;from content.runtime_install import REQUIREMENTS;assert ENTRY.is_file() and REQUIREMENTS.is_file();assert call('+repr(os.environ['CONTENT_MCP_TEST_PYTHON'])+',"health")["state"]=="ready";assert "mcp" not in sys.modules'
            subprocess.run([sys.executable,'-I','-B','-c',script],check=True,timeout=20,capture_output=True)
            self.assertEqual(hashes,{p.relative_to(bundle).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()
