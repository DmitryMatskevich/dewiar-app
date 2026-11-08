"""Immutable credential blobs using Dewiar's existing Vault/device-key format.

One encrypted file per exact reference avoids rewriting an unrelated password
catalog or losing another process's rotation. These files live outside Workspace.
"""
import json
import os
from pathlib import Path
import re
import uuid
from .mcp_helper import CredentialBundle, HelperError


class CredentialVault:
    def __init__(self, root, key_path):
        self.root=Path(root);self.key_path=Path(key_path)

    def _path(self,ref):
        if not isinstance(ref,str) or not re.fullmatch(r'[a-f0-9]{32}:[a-f0-9]{10}',ref):raise HelperError('credential_unavailable')
        path=self.root/(ref.split(':')[0]+'.bin')
        if path.is_symlink():raise HelperError('credential_unavailable')
        return path

    def put(self,bundle,binding):
        if not isinstance(bundle,CredentialBundle):raise HelperError('invalid_credential')
        from vault import Vault
        data=json.dumps({'binding':binding,'credential':bundle.value},ensure_ascii=True,separators=(',',':'),allow_nan=False)
        if len(data.encode())>200*1024:raise HelperError('invalid_credential')
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        path=self.root/(uuid.uuid4().hex+'.bin');tmp=path.with_suffix('.tmp')
        fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        try:
            record=Vault(path,self.key_path).put('Dewiar OAuth',data,item_id=uuid.uuid4().hex)
            ref=path.stem+':'+record['id']
            if self.get(ref,binding).value!=bundle.value:raise HelperError('credential_unavailable')
            with path.open('rb') as f:os.fsync(f.fileno())
            if os.name!='nt':
                fd=os.open(self.root,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
            return ref
        except Exception:
            path.unlink(missing_ok=True);tmp.unlink(missing_ok=True)
            raise HelperError('credential_unavailable') from None

    def get(self,ref,binding):
        from vault import Vault
        path=self._path(ref)
        try:
            if not path.is_file() or path.stat().st_size>256*1024:raise ValueError()
            record=Vault(path,self.key_path).get(ref.split(':')[1])
            if not record:raise ValueError()
            value=json.loads(record['value'])
            if not isinstance(value,dict) or set(value)!={'binding','credential'} or value['binding']!=binding or not isinstance(value['credential'],dict):raise ValueError()
            return CredentialBundle(value['credential'])
        except Exception:raise HelperError('credential_unavailable') from None

    def forget(self,ref,binding):
        # A copied DB may retain another workspace's reference. Possessing that
        # reference does not authorize deleting the original workspace's blob.
        if ref:
            self.get(ref,binding)
            self._path(ref).unlink(missing_ok=True)
