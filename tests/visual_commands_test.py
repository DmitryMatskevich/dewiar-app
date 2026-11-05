"""Isolated OCR geometry/command tests. Synthetic text only, no models or account calls."""
import copy,hashlib,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw,ImageFont
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import visual_commands as C
import ocr
from visual_assets import save

def fixture():
    def word(text,x,y,w):return {'text':text,'confidence':.9,'box':[x/1600,1-(y+40)/900,w/1600,40/900]}
    rows=[('Сохранить проект',[word('Сохранить',900,60,180),word('проект',1090,60,130)]),
          ('Сохранить',[word('Сохранить',950,300,180)]),
          ('Итого 123',[word('Итого',1000,500,100),word('123',1110,500,60)]),
          ('test@example.com',[word('test@example.com',900,650,300)]),
          ('пароль: test-only',[word('пароль:',50,650,120),word('test-only',180,650,120)])]
    return {'status':'done','engine':'synthetic','lines':[{'text':text,'box':C.union([w['box'] for w in words]),'words':words} for text,words in rows]}

class CommandsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.src=self.folder/'source.png';Image.new('RGB',(1600,900),'white').save(self.src)
        self.source={'width':1600,'height':900,'sha256':hashlib.sha256(self.src.read_bytes()).hexdigest()}
        self.doc={'revision':0,'source':self.source,'state':{'width':1600,'height':900,'markers':[]},'crop':None}
        self.index=C.index_result(fixture(),self.source);save(self.folder/'ocr.json',self.index)
    def tearDown(self):self.temp.cleanup()
    def plan(self,text):return C.plan(self.src,self.folder,self.doc,text)
    def test_parse_and_exact_word_geometry(self):
        for text,kind in [('обведи «Итого» красным','FrameMarker'),('обведи овалом «Итого»','EllipseFrameMarker'),('подчеркни «Итого» синим','LineMarker'),('стрелка к «Итого»','ArrowMarker'),('скрой «Итого»','CoverMarker'),('подпиши «Итого» «Проверить сумму»','TextMarker'),('пронумеруй «Итого»','TextMarker')]:
            result=self.plan(text);self.assertFalse(result['review']);self.assertEqual(result['candidates'][0]['marker']['typeName'],kind)
        self.assertEqual(C.parse('обведи Краснодар')['target'],'краснодар')
        result=self.plan('подчеркни Итого красным');c=result['candidates'][0]
        self.assertEqual(c['box'],[1000,500,100,40]);self.assertEqual(c['marker']['strokeColor'],'#e5484d')
        self.assertEqual(self.doc['state']['markers'],[])
    def test_duplicate_and_fuzzy_always_review(self):
        result=self.plan('обведи Сохранить');self.assertTrue(result['review']);self.assertEqual(len(result['candidates']),2)
        self.assertEqual(self.plan('выдели «Сохранить проект»')['candidates'][0]['box'],[900,60,320,40])
        result=self.plan('обведи «Сохраниь»');self.assertTrue(result['review']);self.assertTrue(result['candidates'])
        self.assertFalse(self.plan('обведи «несуществующая надпись»')['candidates'])
        with self.assertRaises(ValueError):self.plan('отправь снимок в интернет')
    def test_private_review_crop_and_existing_covers(self):
        result=self.plan('Скрыть личное');self.assertTrue(result['review']);self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(c['marker']['fillColor']=='#000000' for c in result['candidates']))
        self.doc['state']['markers']=[result['candidates'][0]['marker']]
        self.assertEqual(len(self.plan('Скрыть личное')['candidates']),1)
        self.doc['crop']={'x':0,'y':0,'width':500,'height':500}
        self.assertEqual(self.plan('Скрыть личное')['candidates'],[])
        self.assertTrue(C.luhn('4111 1111 1111 1111'));self.assertFalse(C.luhn('4111 1111 1111 1112'))
    def test_cache_no_repeat_ocr_and_invalidated_hash(self):
        with patch('ocr.recognize_result',return_value=fixture()) as recognize:
            self.plan('обведи «Итого»');recognize.assert_not_called()
            other={**self.source,'sha256':'changed'}
            with self.assertRaisesRegex(ValueError,'Оригинал изменён'):C.get_index(self.src,self.folder,other)
    def test_command_revision_race(self):
        import visual_assets as V
        from types import SimpleNamespace
        record=(None,self.src,self.folder,self.doc)
        changed=(None,self.src,self.folder,{**self.doc,'revision':1})
        with patch.object(V,'read',side_effect=[record,changed]):
            with self.assertRaisesRegex(ValueError,'изменились во время'):V.command(SimpleNamespace(),'visual_command',{'asset_id':'a','base_revision':0,'text':'обведи Итого'})
        with patch.object(V,'read',return_value=record):
            with self.assertRaisesRegex(ValueError,'изменились'):V.command(SimpleNamespace(),'visual_command',{'asset_id':'a','base_revision':4,'text':'обведи Итого'})

    def test_tsv_coordinates(self):
        tsv='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t100\t200\t80\t30\t95\tHello\n5\t1\t1\t1\t1\t2\t200\t200\t60\t30\t90\tworld\n'
        raw=ocr._tesseract_details(tsv,self.src.read_bytes());idx=C.index_result(raw,self.source)
        self.assertEqual(idx['lines'][0]['words'][0]['box'],[100,200,80,30]);self.assertEqual(idx['lines'][0]['box'],[100,200,160,30])
    def test_scoped_voice_never_routes_to_chat_or_desktop(self):
        from background_dictation_test import Engine
        from visual_dictation import command
        engine=Engine();engine.dictation_active=False;engine.server=False;engine.state='idle'
        engine.set_state=lambda state:setattr(engine,'state',state)
        with patch('visual_dictation.threading.Timer'):
            result=command(engine,'visual_dictation_start',{'asset_id':'image-a'})
        sid=result['session'];self.assertEqual(engine.events[-1][0],'visual_dictation')
        engine._dictation_result('Лира, обведи Сохранить красным','synthetic',sid)
        self.assertEqual(engine.events[-1][1]['text'],'Лира, обведи Сохранить красным')
        self.assertTrue(all(event[0]=='visual_dictation' for event in engine.events))
        self.assertFalse(command(engine,'visual_dictation_cancel',{'asset_id':'image-b','session':sid})['ok'])
        self.assertTrue(engine.dictation_active)
        with self.assertRaisesRegex(ValueError,'редакторе снимка'):engine.dictation_start()
        with self.assertRaises(ValueError):command(engine,'visual_dictation_start',{'asset_id':'image-b'})
        command(engine,'visual_dictation_finish',{'asset_id':'image-a','session':sid})
        engine._dictation_done(sid)
        self.assertIsNone(engine._visual_dictation_target);self.assertFalse(engine.dictation_active)
        engine.server=True
        with self.assertRaisesRegex(ValueError,'текстовом режиме'):command(engine,'visual_dictation_start',{'asset_id':'image-a'})
        self.assertIsNone(engine._visual_dictation_target)

    def test_real_vision_word_boxes(self):
        if not ocr.MAC:self.skipTest('Requires Apple Vision')
        image=Image.new('RGB',(1600,900),'white');font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',48)
        ImageDraw.Draw(image).text((100,100),'Сохранить проект',font=font,fill='black');stream=io.BytesIO();image.save(stream,'PNG')
        raw=ocr._vision(stream.getvalue(),strict=True,details=True)
        self.assertIn('Сохранить',raw['text']);self.assertGreaterEqual(len(raw['lines'][0]['words']),2)
        idx=C.index_result(raw,self.source);found=C.matches(idx,'Сохранить')[0]
        self.assertLess(found['box'][2],idx['lines'][0]['box'][2]);self.assertLess(abs(found['box'][0]-100),15)
        if ocr.shutil.which('tesseract'):
            with patch('ocr._vision',side_effect=RuntimeError('synthetic native failure')):
                fallback=ocr.recognize_result(stream.getvalue())
            self.assertEqual(fallback['engine'],'Tesseract');self.assertGreaterEqual(len(fallback['lines'][0]['words']),2)

if __name__=='__main__':unittest.main()
