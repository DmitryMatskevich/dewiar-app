"""Document migration, structural edits, rendering and revision integrity."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.store import ContentStore, SCHEMA_VERSION
from content.models import render_html, render_markdown
from content.diff import compare

def block(bid, text, kind='paragraph', locked=False):
    return dict(id=bid,text=text,type=kind,locked=locked)

class BlocksTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=ContentStore(self.root)

    def test_structure_reorder_diff_restore_and_noop(self):
        blocks=[block('a','Начало','heading'),block('b','Текст',locked=True)]
        row=self.store.create({'title':'Статья','blocks':blocks},'a')
        moved=self.store.update(row['id'],1,{'blocks':[blocks[1],block('a','Новое начало','heading')]})
        self.assertEqual(moved['body'],'Текст\n\nНовое начало')
        diff=compare(self.store,row['id'],1,2)
        self.assertEqual(len(diff['blocks']),2);self.assertTrue(all(b['moved'] for b in diff['blocks']))
        self.assertIn('+Новое начало',diff['text'])
        restored=self.store.update(row['id'],2,{},restore=1)
        self.assertEqual(restored['blocks'],blocks);self.assertEqual(restored['revision'],3)
        self.assertEqual(self.store.update(row['id'],3,{'blocks':blocks})['revision'],3)
        self.assertNotIn('blocks',self.store.list()['items'][0])

    def test_invalid_block_shapes_and_flattening_rejected(self):
        a=block('a','x')
        for blocks in [[],[a,a],[{**a,'locked':'yes'}],[{**a,'type':'script'}],[{**a,'id':'<x>'}],[{**a,'extra':0}]]:
            with self.assertRaises(ValueError):self.store.create({'title':'x','blocks':blocks},'bad')
        row=self.store.create({'title':'x','blocks':[a,block('b','y')]},'good')
        with self.assertRaises(ValueError):self.store.update(row['id'],1,{'body':'would flatten'})
        with self.assertRaises(ValueError):self.store.update(row['id'],1,{'body':'mismatch','blocks':[a]})
        self.assertEqual(self.store.get(row['id'])['revision'],1)

    def test_render_is_inert_and_preserves_structure(self):
        blocks=[block('h','<script>','heading'),block('q','Цитата','quote'),block('l','один\nдва','list')]
        html=render_html(blocks)
        self.assertIn('<h2>&lt;script&gt;</h2>',html);self.assertIn('<blockquote>Цитата</blockquote>',html)
        self.assertIn('<li>два</li>',html);self.assertNotIn('<script>',html)
        self.assertEqual(render_html([block('p','Один\n\nДва')]),'<p>Один<br><br>Два</p>')
        md=render_markdown(blocks);self.assertIn('## &lt;script&gt;',md);self.assertIn('- два',md)

    def test_v1_and_v2_migrate_preserving_history(self):
        for version in (1,2):
            root=self.root/str(version);root.mkdir();path=root/'content.db'
            snapshot={'id':'old','kind':'article','revision':1,'created_at':'2026','updated_at':'2026','archived':False,'title':'Старое','author':'','body':'строка\n\nхвост','site_id':None}
            raw=json.dumps(snapshot,ensure_ascii=False)
            with sqlite3.connect(path) as c:
                c.executescript('CREATE TABLE materials(id TEXT PRIMARY KEY,kind TEXT,revision INTEGER,created_at TEXT,updated_at TEXT,archived INTEGER,title TEXT,author TEXT,body TEXT,site_id TEXT); CREATE TABLE revisions(material_id TEXT,revision INTEGER,snapshot TEXT,created_at TEXT,PRIMARY KEY(material_id,revision)); CREATE TABLE request_keys(key TEXT PRIMARY KEY,command TEXT,payload_hash TEXT,result TEXT); CREATE TABLE site_profiles(id TEXT PRIMARY KEY,domain TEXT,display_name TEXT,adapter TEXT,connection_status TEXT);')
                c.execute('INSERT INTO materials VALUES (:id,:kind,:revision,:created_at,:updated_at,:archived,:title,:author,:body,:site_id)',snapshot)
                c.execute('INSERT INTO revisions VALUES (?,?,?,?)',('old',1,raw,'2026'))
                if version==2:c.execute('CREATE TABLE sources(material_id TEXT PRIMARY KEY,snapshot TEXT)')
                c.execute(f'PRAGMA user_version={version}')
            store=ContentStore(root)
            self.assertEqual(store.get('old')['body'],snapshot['body'])
            self.assertEqual(store.revision('old',1)['blocks'][0]['text'],snapshot['body'])
            store.update('old',1,{'blocks':[block('a','Новая')]})
            restored=store.update('old',2,{},restore=1);self.assertEqual(restored['body'],snapshot['body'])
            with sqlite3.connect(path) as c:
                self.assertEqual(c.execute('SELECT snapshot FROM revisions WHERE revision=1').fetchone()[0],raw)
                self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)

if __name__=='__main__':unittest.main()
