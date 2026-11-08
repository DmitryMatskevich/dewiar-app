import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore
from content.commands import dispatch

class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(Path(self.tmp.name)/'one');self.target=ContentStore(Path(self.tmp.name)/'two')
        self.row=dispatch(self.store,'content_import_html',{'html':'<p>Переносимый текст</p>','title':'Статья','author':'Автор','request_key':'html'})
        self.package=dispatch(self.store,'content_export',{'id':self.row['id'],'revision':1})
    def files(self):
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(self.package['b64']))) as z:return {n:z.read(n) for n in z.namelist()}
    def archive(self,files,rehash=False):
        if rehash:
            manifest=json.loads(files['manifest.json']);manifest['files']={n:hashlib.sha256(v).hexdigest() for n,v in files.items() if n!='manifest.json'};files['manifest.json']=json.dumps(manifest).encode()
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            for n,v in files.items():z.writestr(n,v)
        return base64.b64encode(buf.getvalue()).decode()
    def import_(self,b64):return dispatch(self.target,'content_import_package',{'b64':b64,'request_key':'import'})
    def test_roundtrip_new_id_preserves_blocks_and_original(self):
        row=self.import_(self.package['b64']);self.assertNotEqual(row['id'],self.row['id']);self.assertEqual(row['blocks'],self.row['blocks'])
        self.assertEqual(row['revision'],1);self.assertEqual(self.import_(self.package['b64']),row)
        original=dispatch(self.target,'content_html_original',{'id':row['id']});self.assertEqual(base64.b64decode(original['b64']),'<p>Переносимый текст</p>'.encode())
        self.assertFalse(dispatch(self.target,'content_review',{'id':row['id']})['ready'])
    def test_hash_mismatch_and_traversal_rejected_without_material(self):
        files=self.files();files['article.html']=b'altered'
        with self.assertRaisesRegex(ValueError,'сумма'):self.import_(self.archive(files))
        files=self.files();files['../outside']=b'no'
        with self.assertRaisesRegex(ValueError,'путь'):self.import_(self.archive(files,True))
        self.assertEqual(self.target.list()['total'],0)
    def test_missing_asset_bad_base64_and_oversized_member(self):
        files=self.files();files.pop(next(n for n in files if n.startswith('assets/')))
        with self.assertRaisesRegex(ValueError,'оригинала'):self.import_(self.archive(files,True))
        with self.assertRaises(ValueError):self.import_('invalid !')
        files=self.files();files['article.html']=b'0'*(8*1024*1024+1)
        with self.assertRaisesRegex(ValueError,'лимит'):self.import_(self.archive(files,True))
    def test_forged_body_does_not_replace_validated_blocks(self):
        files=self.files();row=json.loads(files['material.json']);row['body']='different';files['material.json']=json.dumps(row).encode()
        with self.assertRaisesRegex(ValueError,'body'):self.import_(self.archive(files,True))

if __name__=='__main__':unittest.main()
