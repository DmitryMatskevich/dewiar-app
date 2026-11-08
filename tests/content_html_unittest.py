"""Untrusted HTML never becomes executable in preview/export; originals survive."""
import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore
from content.commands import dispatch

class HTMLTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ContentStore(self.tmp.name)
    def create(self,html,key='html'):
        return dispatch(self.store,'content_import_html',{'html':html,'title':'HTML','request_key':key})
    def test_simple_structure_roundtrip_original_and_manifest(self):
        raw='<article><h2>Заголовок</h2><p>Текст &amp; данные<br>Строка</p><ul><li>Один</li><li>Два</li></ul></article>'
        row=self.create(raw)
        self.assertEqual([b['type'] for b in row['blocks']],['heading','paragraph','list'])
        result=dispatch(self.store,'content_export',{'id':row['id'],'revision':1})
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['b64']))) as z:
            names=[n for n in z.namelist() if n.startswith('assets/')]
            self.assertEqual(z.read(names[0]).decode(),raw)
            self.assertIn('<h2>Заголовок</h2>',z.read('article.html').decode())
        self.assertEqual(self.create(raw)['id'],row['id'])
    def test_active_and_unsupported_html_stays_opaque(self):
        raw='<p>Безопасный текст</p><script>alert(1)</script><img src=x onerror=alert(2)><a href="javascript:alert(3)">ссылка</a><form action="https://example.invalid"><input name=x></form>'
        row=self.create(raw)
        self.assertEqual(len(row['blocks']),5)
        self.assertTrue(all(b['type']=='unsupported' and b['locked'] for b in row['blocks'][1:]))
        self.assertNotIn('alert',row['body'])
        original=dispatch(self.store,'content_html_original',{'id':row['id']})
        self.assertEqual(base64.b64decode(original['b64']).decode(),raw)
        with self.assertRaisesRegex(ValueError,'неподдерживаемый'):dispatch(self.store,'content_export',{'id':row['id'],'revision':1})
    def test_malformed_nested_and_unknown_format_not_silently_flattened(self):
        for i,raw in enumerate(['<p><b>Жирный</b></p>','<p>Не закрыт','<h3>Уровень 3</h3>','<ol><li>Нумерация</li></ol>']):
            row=self.create(raw,str(i));self.assertEqual(row['blocks'][0]['type'],'unsupported')
        with self.assertRaises(ValueError):self.create('<div>'*100+'x'+'</div>'*100,'deep')
    def test_copy_preserves_original_and_missing_asset_fails(self):
        row=self.create('<p>Редактируемый</p>')
        copied=dispatch(self.store,'content_copy',{'id':row['id'],'data':{'title':'Копия','blocks':row['blocks']},'request_key':'copy'})
        self.assertEqual(self.store.sources(copied['id']),self.store.sources(row['id']))
        source=self.store.sources(row['id'])[0];(self.store.root/'assets'/source['asset_id']).unlink()
        with self.assertRaisesRegex(ValueError,'недоступен'):dispatch(self.store,'content_export',{'id':copied['id'],'revision':1})
    def test_unknown_original_and_unlock_rejected(self):
        row=self.create('<form></form>');b=row['blocks'][0]
        for altered in [{**b,'locked':False},{**b,'original_ref':'0'*64}]:
            with self.assertRaises(ValueError):self.store.update(row['id'],1,{'blocks':[altered]})

if __name__=='__main__':unittest.main()
