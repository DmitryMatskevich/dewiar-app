"""Complete memory and model raster targets. Isolated fixture; no live provider."""
from canvas_chat_test import *
from canvas_context import build,pages
class ContextTests(ChatTests):
 def image(self):
  from PIL import Image
  folder,doc=C.read(self.engine,self.doc['id']);Image.new('RGB',(200,100),'white').save(folder/'pic.png')
  import hashlib
  doc['elements']=[{**C.image_element({'id':'pic','width':200,'height':100}),'id':'img'}]
  doc['files']={'pic':{'id':'pic','path':'pic.png','width':200,'height':100,'sha256':hashlib.sha256((folder/'pic.png').read_bytes()).hexdigest()}};C.save(folder/'state.json',doc)
  return folder,doc
 def index(self):return {'status':'done','engine':'fixture','lines':[{'id':'l0','text':'Дело было ночью','box':[10,20,110,15],'words':[{'id':'w0','text':'Дело','box':[10,20,30,15]},{'id':'w1','text':'было','box':[45,20,30,15]},{'id':'w2','text':'ночью','box':[80,20,40,15]}]}]}
 def test_selected_model_still_local_ocr(self):
  self.image()
  with patch('visual_commands.get_index',return_value=self.index()),patch('llm.stream_chat') as model:
   result=self.call(text='Обведи «Дело было ночью»',profile_id='test-local',use_model=True)
   self.assertEqual(len(result['ocr']['candidates']),1);model.assert_not_called()
 def test_model_context_contains_ocr_and_resolves_raster(self):
  self.image();response=json.dumps({'reply':'Обведём заголовок','operations':[{'op':'outline','target':{'text':'Дело было ночью'},'color':'#e5484d'}]})
  with patch('visual_commands.get_index',return_value=self.index()),patch('canvas_context.get_index',return_value=self.index()),patch('llm.stream_chat',return_value=iter([response])) as model:
   result=self.call(text='Пожалуйста, пометь заголовок',profile_id='test-local',use_model=True)
   payload=json.loads(model.call_args.args[2][0]['content']);self.assertIn('Дело было ночью',json.dumps(payload,ensure_ascii=False));self.assertEqual(result['ocr']['candidates'][0]['text'],'Дело было ночью')
 def test_full_scene_no_truncation_and_page_reads(self):
  folder,doc=C.read(self.engine,self.doc['id']);doc['elements']=[{**doc['elements'][0],'id':f'e{i}','text':'x'*800+f' UNIQUE_{i}'} for i in range(120)]
  numbers={e['id']:i+1 for i,e in enumerate(doc['elements'])};memory=build(folder,doc,numbers);chunks=pages(memory)
  self.assertEqual(len(memory['objects']),120);self.assertIn('UNIQUE_119',json.dumps(chunks));self.assertGreater(len(chunks),1)
  with patch('llm.stream_chat',side_effect=[iter([json.dumps({'read_pages':[len(chunks)-1]})]),iter([json.dumps({'reply':'done','operations':[]})])]) as model:
   result=CC.ask_model(self.engine,'test-local','Найди последний объект',CC.rows(doc,{'numbers':numbers}),[],[],memory=memory)
   self.assertEqual(result['reply'],'done');self.assertIn('UNIQUE_119',model.call_args.args[2][0]['content'])
 def test_target_clarification(self):
  self.image();CC.command(self.engine,'canvas_chat_log',{**self.a,'role':'user','text':'Обведи текст.'})
  with patch('visual_commands.get_index',return_value=self.index()):self.assertEqual(len(self.call(text='Дело было ночью.')['ocr']['candidates']),1)
 def test_real_russian_ocr_in_model_context(self):
  fixture=os.environ.get('DEWIAR_OCR_FIXTURE')
  if not fixture:self.skipTest('No real image fixture supplied')
  self.engine.emit=lambda *args,**kwargs:None
  result=C.from_screenshot(self.engine,None,Path(fixture).read_bytes(),'fixture','ТЕСТ — отдельные данные');self.a={'canvas_id':result['canvas_id'],'base_revision':C.read(self.engine,result['canvas_id'])[1]['revision'],'selected_ids':[]}
  response=json.dumps({'reply':'Найдена надпись','operations':[{'op':'underline','target':{'text':'Дело было ночью'},'color':'#e5484d'}]})
  with patch('llm.stream_chat',return_value=iter([response])) as model:
   result=self.call(text='Пожалуйста, отметь название стихотворения',profile_id='test-local',use_model=True)
   self.assertEqual(len(result['ocr']['candidates']),2)
   self.assertIn('Дело было ночью',model.call_args.args[2][0]['content'])
 def test_spoken_phrase_and_split_ocr_row(self):
  from visual_commands import matches
  op=CC.parse('Обвести словосочетания. Марк Котляр.',[])[0];self.assertEqual(op['target']['text'],'марк котляр')
  index={'lines':[{'id':'a','text':'Марк','box':[10,10,50,20],'words':[]},{'id':'b','text':'Котляр','box':[65,10,80,20],'words':[]}]}
  found=matches(index,'Марк Котляр');self.assertTrue(found[0]['exact']);self.assertEqual(found[0]['box'],[10,10,135,20])
 def test_artist_auto_selection_and_no_general_fallback(self):
  self.settings.update({'llm':{'profiles':{'test-local':{'kind':'openai','base_url':'http://127.0.0.1:1234/v1','model':'local','roles':['images'],'priority':2},'artist-first':{'kind':'openai','base_url':'http://localhost:1234','model':'first','roles':['images'],'priority':1}}}})
  response=json.dumps({'reply':'готово','operations':[]})
  with patch('llm.stream_chat',side_effect=[RuntimeError('offline'),iter([response])]) as model:
   result=self.call(text='Предложи оформление',auto_model=True)
   self.assertTrue(result.get('model_used'),(result,CC.artist_profiles(self.engine),model.call_args_list));self.assertEqual(model.call_count,2);self.assertIn('test-local',result['reply'])
 def test_no_artist_shows_setup_only_when_needed(self):
  with patch('llm.stream_chat') as model:
   self.assertTrue(self.call(text='Предложи оформление',auto_model=True)['needs_model_setup']);model.assert_not_called()
   self.assertEqual(self.call(text='Обведи «Итого»',auto_model=True)['operations'][0]['op'],'outline')
 def test_artist_blocked_disabled_and_local_only(self):
  self.settings.update({'llm':{'profiles':{'test-cloud':{'kind':'openai','base_url':'https://example.invalid/v1','model':'test','roles':['images']},'test-local':{'kind':'openai','base_url':'http://127.0.0.1:1234','model':'local','roles':['images'],'routing_enabled':False}}}})
  with patch('egress.local_only',return_value=True):self.assertEqual(CC.artist_profiles(self.engine),[])
  with patch('modelrouter.blocked',return_value=True):self.assertEqual(CC.artist_profiles(self.engine),[])
if __name__=='__main__':unittest.main()
