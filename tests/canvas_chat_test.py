"""Local chat and explicit model selection. Isolated state; no live models/network."""
import os,sys,tempfile,json,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
TEMP=tempfile.TemporaryDirectory(prefix='dewiar-chat-')
os.environ['DEWIAR_CONFIG_DIR']=TEMP.name+'/config';os.environ['DEWIAR_STATE_DIR']=TEMP.name+'/state'
ROOT=Path(os.environ.get('DEWIAR_CANVAS_ROOT',Path(__file__).resolve().parents[1]));sys.path.insert(0,str(ROOT/'core'))
from settings import Settings
from workspace import Workspace
import canvases as C
import canvas_chat as CC

class ChatTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=TEMP.name);self.addCleanup(self.temp.cleanup)
  self.settings=Settings(Path(self.temp.name)/'settings.json');self.settings.update({'workspace':{'path':self.temp.name+'/workspace'},'llm':{'profiles':{'test-cloud':{'kind':'openai','name':'ТЕСТ','base_url':'https://example.invalid/v1','model':'test','_route_candidates':[{'bad':'fallback'}]},'test-local':{'kind':'openai','base_url':'http://127.0.0.1:1234/v1','model':'local'}}}})
  self.engine=SimpleNamespace(s=self.settings,workspace=Workspace(self.settings));self.doc=C.create(self.engine,'ТЕСТ — отдельные данные')
  folder,doc=C.read(self.engine,self.doc['id']);doc['elements']=[{**C.image_element({'id':'unused','width':100,'height':40}),'id':'label','type':'text','text':'Итого','x':120,'y':240}];C.save(folder/'state.json',doc)
  self.a={'canvas_id':doc['id'],'base_revision':0,'selected_ids':[]}
 def call(self,**args):return CC.command(self.engine,'canvas_chat_plan',{**self.a,**args})
 def test_local_does_not_call_model(self):
  with patch('llm.stream_chat',side_effect=AssertionError('model forbidden')) as model:
   self.assertEqual(self.call(text='Выдели текст Итого')['operations'][0]['op'],'outline')
   answer=self.call(text='Обведи текст «Итого» красным');self.assertEqual(answer['operations'][0]['target'],{'ids':['label']});model.assert_not_called()
 def test_empty_selection_and_ambiguous_target(self):
  with self.assertRaisesRegex(ValueError,'Выделите объект'):self.call(text='Обведи')
  folder,doc=C.read(self.engine,self.doc['id']);doc['elements'].append({**doc['elements'][0],'id':'second'});C.save(folder/'state.json',doc)
  with self.assertRaisesRegex(ValueError,'несколько'):self.call(text='Удали «Итого»')
 def test_local_add_move_color_background(self):
  for text,op in [('Добавь текст «Готово» в точке 120, 240','text'),('Перемести #1 в 400, 180','move'),('Измени цвет #1 на синий','color'),('Сделай фон белым','background')]:self.assertEqual(self.call(text=text)['operations'][0]['op'],op)
 def test_model_requires_explicit_dispatch(self):
  with patch('llm.stream_chat') as stream:
   with self.assertRaisesRegex(ValueError,'явно'):self.call(text='помоги',profile_id='test-cloud')
   stream.assert_not_called()
 def test_external_forbidden_in_local_mode(self):
  with patch('egress.local_only',return_value=True),patch('llm.stream_chat') as stream:
   with self.assertRaisesRegex(ValueError,'запрещена'):self.call(text='помоги',profile_id='test-cloud',use_model=True)
   stream.assert_not_called()
 def test_model_single_profile_no_images_no_fallback(self):
  response=json.dumps({'reply':'Предлагаю синий цвет','operations':[{'op':'color','target':{'numbers':[1]},'color':'#1971c2'}]})
  with patch('egress.local_only',return_value=False),patch('llm.stream_chat',return_value=iter([response])) as stream:
   answer=self.call(text='сделай синим',profile_id='test-cloud',use_model=True);self.assertEqual(answer['operations'][0]['target']['ids'],['label']);stream.assert_called_once()
   p,_,messages=stream.call_args.args;self.assertNotIn('_route_candidates',p);self.assertNotIn('data:image',json.dumps(messages));self.assertNotIn('files',json.loads(messages[0]['content']))
  with patch('egress.local_only',return_value=False),patch('llm.stream_chat',side_effect=RuntimeError('provider error')) as stream:
   with self.assertRaisesRegex(RuntimeError,'provider error'):self.call(text='помоги',profile_id='test-cloud',use_model=True)
   stream.assert_called_once()
 def test_reject_model_code_and_unknown_target(self):
  for operation in ({'op':'run_code','code':'delete files'},{'op':'delete','target':{'ids':['absent']}},{'op':'background','color':'red','execute':True}):
   with patch('egress.local_only',return_value=False),patch('llm.stream_chat',return_value=iter([json.dumps({'reply':'x','operations':[operation]})])):
    with self.assertRaises(ValueError):self.call(text='x',profile_id='test-cloud',use_model=True)
 def test_history_and_stable_numbers(self):
  CC.command(self.engine,'canvas_chat_log',{**self.a,'role':'user','text':'Привет'})
  state=CC.command(self.engine,'canvas_chat_state',self.a);self.assertEqual(state['messages'][0]['text'],'Привет');self.assertEqual(state['objects'][0]['number'],1)
  folder,doc=C.read(self.engine,self.doc['id']);doc['elements'].insert(0,{**doc['elements'][0],'id':'new'});C.save(folder/'state.json',doc)
  state=CC.command(self.engine,'canvas_chat_state',self.a);self.assertEqual([r['number'] for r in state['objects']],[2,1])
 def test_local_ocr_maps_image_coordinates(self):
  folder,doc=C.read(self.engine,self.doc['id']);doc['elements']=[{**C.image_element({'id':'pic','width':200,'height':100}),'id':'img','x':100,'y':200}];doc['files']={'pic':{'id':'pic','path':'fixture.png','width':100,'height':50,'sha256':'synthetic'}};C.save(folder/'state.json',doc)
  index={'status':'done','lines':[{'text':'Итого','words':[{'id':'w','text':'Итого','box':[10,5,30,10]}]}]}
  with patch('visual_commands.get_index',return_value=index),patch('llm.stream_chat') as model:
   result=self.call(text='Обведи «Итого»');c=result['ocr']['candidates'][0];self.assertEqual((c['x'],c['y'],c['width'],c['height']),(120,210,60,20));model.assert_not_called()
 def test_button_raster_delete_and_stale_rejected(self):
  with self.assertRaisesRegex(ValueError,'Граница кнопки'):self.call(text='Обведи кнопку «Итого»')
  with self.assertRaisesRegex(ValueError,'изменился'):self.call(text='Привет',base_revision=3)
 def test_mixed_quotes_delete(self):
  self.assertEqual(CC.parse('Удали «Я спросил"',[]),[{'op':'delete','target':{'text':'Я спросил'}}])
 def test_flat_background_and_texture(self):
  from PIL import Image,ImageDraw
  p=Path(self.temp.name)/'background.png';im=Image.new('RGB',(100,70),'#fafafa');ImageDraw.Draw(im).rectangle((20,20,65,40),fill='black');im.save(p)
  self.assertEqual(CC.flat_background(p,[20,20,45,20]),'#fafafa')
  Image.frombytes('RGB',(100,70),os.urandom(21000)).save(p)
  with self.assertRaisesRegex(ValueError,'неоднородный'):CC.flat_background(p,[20,20,45,20])
 def test_ocr_delete_selection_lock_and_rotation(self):
  from PIL import Image
  folder,doc=C.read(self.engine,self.doc['id']);Image.new('RGB',(200,100),'white').save(folder/'fixture.png')
  doc['elements']=[{**C.image_element({'id':'pic','width':200,'height':100}),'id':'img','x':100,'y':200,'angle':.5}];doc['files']={'pic':{'id':'pic','path':'fixture.png','width':200,'height':100,'sha256':'synthetic'}};C.save(folder/'state.json',doc)
  index={'status':'done','lines':[{'text':'Я спросил','words':[{'id':'a','text':'Я','box':[10,20,10,10]},{'id':'b','text':'спросил','box':[22,20,45,10]}]}]}
  with patch('visual_commands.get_index',return_value=index),patch('llm.stream_chat') as model:
   result=self.call(text='Удали «Я спросил"',selected_ids=['unrelated-rectangle']);ocr=result['ocr'];self.assertTrue(ocr['review']);self.assertEqual(ocr['op'],'cover');self.assertEqual(ocr['candidates'][0]['color'],'#ffffff');self.assertEqual(ocr['candidates'][0]['shape']['angle'],.5)
   result=self.call(text='Закрась «Я спросил» синим');self.assertEqual(result['ocr']['candidates'][0]['color'],'#1971c2');model.assert_not_called()
   doc['elements'][0]['locked']=True;C.save(folder/'state.json',doc)
   with self.assertRaisesRegex(ValueError,'заблокирована'):self.call(text='Удали «Я спросил»')

if __name__=='__main__':
 try:unittest.main()
 finally:TEMP.cleanup()
