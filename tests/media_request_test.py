"""Video intent regression: the screenshot request, real task creation and shortcut precedence."""
import json,sys,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import media_intent as I
import music,voice_commands
from engine_music import MusicMixin
from tasks import TaskManager
URL='https://www.youtube.com/watch?v=BmVC0c_kcVU'
class TestMediaRequest(unittest.TestCase):
 def engine(self):
  e=SimpleNamespace(toolbox=SimpleNamespace(policy=lambda _: (True,False)),tasks=SimpleNamespace(list=Mock(return_value=[]),create_transcription=Mock(return_value=SimpleNamespace(title='Транскрибация'))),_say_quick=Mock(),history=[])
  return e
 def test_screenshot_and_aliases_cannot_open_music(self):
  for phrase in ['Давай сделаем транскрибацию ролика.'+URL,'Хочу транскрипцию '+URL,'Извлеки текст '+URL,'Извлеки голос '+URL,'Открой '+URL]:
   self.assertIsNone(music.parse(phrase,asked=True));self.assertIsNone(voice_commands.parse(phrase))
  self.assertIsNotNone(music.parse('Давай джаз'))
 def test_start_is_real_before_announcement(self):
  e=self.engine();events=[]
  e.tasks.create_transcription.side_effect=lambda *a,**k: (events.append('create') or SimpleNamespace(title='Транскрибация'))
  e._say_quick.side_effect=lambda *a,**k:events.append('announce')
  self.assertTrue(MusicMixin._video_request(e,'Давай сделаем транскрибацию ролика.'+URL))
  self.assertEqual(events,['create','announce'])
 def test_bare_link_and_followup(self):
  e=self.engine();self.assertTrue(MusicMixin._video_request(e,URL));e.tasks.create_transcription.assert_not_called()
  question=e._say_quick.call_args.args[0]
  for option in ['открыть для просмотра','скачать видеофайл','обработать']:self.assertIn(option,question)
  self.assertRegex(question,r'расшифровать речь|извлечь текст')
  MusicMixin._video_request(e,'Извлеки текст');e.tasks.create_transcription.assert_called_once_with([URL],goal='Извлеки текст')
 def test_all_followup_choices_keep_the_offered_source(self):
  import time
  for reply,action in [('посмотреть','open'),('скачать','download'),('расшифровать','transcribe'),('конспект','process')]:
   e=self.engine();MusicMixin._video_request(e,URL)
   resolved=I.resolve_followup(reply,e._video_pending,'chat:legacy',time.monotonic())
   self.assertIn(URL,resolved);self.assertEqual(I.intent(resolved),action)
   handled=MusicMixin._video_request(e,resolved)
   if action=='transcribe':
    self.assertTrue(handled);e.tasks.create_transcription.assert_called_once()
   else:
    self.assertFalse(handled);e.tasks.create_transcription.assert_not_called()
    self.assertIsNone(e._video_pending)
 def test_generic_processing_asks_for_the_expected_result(self):
  import time
  e=self.engine();MusicMixin._video_request(e,URL)
  text=I.resolve_followup('обработать',e._video_pending,'chat:legacy',time.monotonic())
  self.assertTrue(MusicMixin._video_request(e,text));self.assertIn('Как обработать',e._say_quick.call_args.args[0])
  e.tasks.create_transcription.assert_not_called()
  text=I.resolve_followup('Сделай конспект',e._video_pending,'chat:legacy',time.monotonic())
  self.assertIn(URL,text);self.assertFalse(MusicMixin._video_request(e,text))
 def test_yes_cancel_and_question_do_not_start_an_unselected_action(self):
  e=self.engine();MusicMixin._video_request(e,URL)
  self.assertTrue(MusicMixin._video_request(e,'да'));self.assertEqual(e._say_quick.call_args.args[0],I.CHOICE_QUESTION)
  self.assertTrue(MusicMixin._video_request(e,'не надо'));self.assertIsNone(e._video_pending)
  e.tasks.create_transcription.assert_not_called()
  for text in ['Как скачать '+URL,'Не скачивай '+URL]:self.assertNotEqual(I.intent(text),'download')
 def test_pending_link_does_not_cross_chats_or_expiration(self):
  pending={'scope':'chat:one','time':100,'urls':[URL]}
  for scope,now in [('chat:two',101),('chat:one',701)]:
   self.assertEqual(I.resolve_followup('скачать',pending,scope,now),'скачать')
  self.assertEqual(I.resolve_followup('скачать',None,'chat:one',101),'скачать')
 def test_text_then_link_and_open_not_transcription(self):
  e=self.engine();MusicMixin._video_request(e,'Давай сделаем транскрибацию')
  e.tasks.create_transcription.assert_not_called();MusicMixin._video_request(e,URL)
  e.tasks.create_transcription.assert_called_once()
  for phrase in ['Открой '+URL,'Открой '+URL+' и не транскрибируй','Что такое транскрибация?']:
   e=self.engine();self.assertFalse(MusicMixin._video_request(e,phrase));e.tasks.create_transcription.assert_not_called()
 def test_status_question_reads_real_registry_without_relaunch(self):
  for phrase in ['У тебя запущен процесс транскрибации?','Как идёт транскрибация?','Почему транскрибация не запустилась?']:
   e=self.engine();self.assertTrue(MusicMixin._video_request(e,phrase))
   self.assertIn('не создана',e._say_quick.call_args.args[0]);e.tasks.create_transcription.assert_not_called()
  for state,extra,expected in [('running',{'activity':'Распознано 1:00 из 5:00'},'Распознано 1:00'),('done',{'outputs':[{},{}]},'завершена'),('failed',{'error':'HTTP 403'},'HTTP 403')]:
   e=self.engine();e.tasks.list.return_value=[dict(workflow='media-transcription',title='Речь',status=state,**extra)]
   MusicMixin._video_request(e,'У тебя запущен процесс транскрибации?')
   self.assertIn(expected,e._say_quick.call_args.args[0]);e.tasks.create_transcription.assert_not_called()
 def test_no_false_started_on_error_and_policy(self):
  e=self.engine();e.tasks.create_transcription.side_effect=RuntimeError('disk full');MusicMixin._video_request(e,'Транскрибация '+URL)
  self.assertIn('Не удалось запустить',e._say_quick.call_args.args[0])
  e=self.engine();e.toolbox.policy=lambda _:(False,False)
  self.assertFalse(MusicMixin._video_request(e,'Извлеки текст '+URL));e.tasks.create_transcription.assert_not_called()
 def test_native_workflow_without_language_model_and_dedup(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder).resolve();manager=TaskManager.__new__(TaskManager)
   manager._lock=threading.RLock();manager.tasks={};manager._emit=lambda t:None
   manager.host=SimpleNamespace(workspace=SimpleNamespace(root=root,ensure=lambda:root),persona_name=lambda:'Лира',announce=Mock())
   entered=threading.Event();finish=threading.Event()
   def work(t,name,args):
    entered.set();finish.wait(3)
    report=t.dir/'outputs/transcripts/batch-results.json';report.parent.mkdir(parents=True)
    report.write_text(json.dumps([{'status':'ok'}]))
    output=t.dir/'outputs/text.txt';output.write_text('Готов текст');t.data['outputs']=[{'path':'outputs/text.txt'}]
    return 'Готов текст'
   manager._work=work
   with patch.object(manager,'_run',side_effect=AssertionError('LLM must not run')):
    task=manager.create_transcription([URL]);self.assertTrue(entered.wait(2))
    self.assertIs(manager.create_transcription([URL]),task)
    finish.set();task.thread.join(4)
    self.assertEqual(task.status,'done');self.assertEqual(task.data['workflow'],'media-transcription')
    self.assertEqual(json.loads((task.dir/'задача.json').read_text())['status'],'done')
    self.assertIs(manager.create_transcription(['https://youtube.com/watch?v=BmVC0c_kcVU&t=20']),task)
if __name__=='__main__':
 r=unittest.main(exit=False);print('ALL OK' if r.result.wasSuccessful() else 'FAIL');sys.exit(not r.result.wasSuccessful())
