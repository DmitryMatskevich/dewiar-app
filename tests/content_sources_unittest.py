import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore
from content.sources import snapshot,available
from content.commands import dispatch

class SourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.store=ContentStore(root/'store')
        self.task=root/'task';(self.task/'outputs').mkdir(parents=True)
        self.file=self.task/'outputs/transcript.md';self.file.write_text('00:00 Исходный текст <script>alert(1)</script>',encoding='utf-8')
        self.tasks={'task-id':SimpleNamespace(dir=self.task,data={'title':'Доклад','workflow':'media-transcription','status':'done','outputs':[{'path':'outputs/transcript.md'}]})}
    def test_snapshot_independent_and_metadata(self):
        row=dispatch(self.store,'content_import_source',{'task_id':'task-id','path':'outputs/transcript.md','request_key':'k'},tasks=self.tasks)
        self.file.write_text('Изменённый источник')
        self.assertIn('Исходный',self.store.sources(row['id'])[0]['text'])
        refs=dispatch(self.store,'content_source_refs',{'id':row['id']});self.assertNotIn('text',refs[0])
        self.assertEqual(row['author'],'')
    def test_unregistered_and_escaping_symlink(self):
        with self.assertRaises(ValueError):snapshot(self.tasks,'task-id','../other.txt')
        self.file.unlink();self.file.symlink_to(Path(self.tmp.name)/'outside.txt');self.file.resolve().write_text('secret')
        self.assertEqual(available(self.tasks),[])
        with self.assertRaises(ValueError):snapshot(self.tasks,'task-id','outputs/transcript.md')
    def test_running_task_not_imported(self):
        self.tasks['task-id'].data['status']='running'
        self.assertEqual(available(self.tasks),[])
        with self.assertRaises(ValueError):snapshot(self.tasks,'task-id','outputs/transcript.md')
    def test_export_hashes_and_html_escaping(self):
        row=dispatch(self.store,'content_import_source',{'task_id':'task-id','path':'outputs/transcript.md','request_key':'k'},tasks=self.tasks)
        result=dispatch(self.store,'content_export',{'id':row['id'],'revision':1})
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['b64']))) as z:
            manifest=json.loads(z.read('manifest.json'))
            for name,digest in manifest['files'].items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),digest)
            self.assertNotIn(b'<script>',z.read('article.html'));self.assertIn(b'&lt;script&gt;',z.read('article.html'))
            self.assertEqual(json.loads(z.read('sources.json'))[0]['task_id'],'task-id')
    def test_empty_and_large_source(self):
        for text in ['','я'*600000]:
            self.file.write_text(text)
            with self.assertRaises(ValueError):snapshot(self.tasks,'task-id','outputs/transcript.md')

if __name__=='__main__':unittest.main()
