"""Russian clean Markdown: meaning preservation, phrase continuity and review provenance."""
import sys,json,tempfile,unittest,re
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from transcript_editor import edit,ensure_clean,VERSION
class EditorTest(unittest.TestCase):
 def clean(self,text):return edit([(12,25,text)],'Проверка')
 def test_vocals_fillers_and_phrase_continuity(self):
  r=edit([(0,2,'Ну, ээ, вот, резервный'),(2,6,'ввод ввод ввод работает, да.')],'Проверка')
  self.assertEqual(r['markdown'],'# Проверка\n\nРезервный ввод работает.\n')
  self.assertGreaterEqual(r['removed_count'],4)
  self.assertNotRegex(r['markdown'],r'\d{2}:\d{2}|-->')
 def test_measurements_and_word_boundaries(self):
  r=self.clean('Толщина 10 мм, зазор 1,5 мм. Размеры в мм. Давление 10 мм рт. ст. Тампер и значение параметра сохраняются.')
  self.assertIn('10 мм',r['markdown']);self.assertIn('1,5 мм',r['markdown'])
  self.assertIn('Тампер',r['markdown']);self.assertIn('значение параметра',r['markdown'])
  self.assertIn('Размеры в мм.',r['markdown'])
  self.assertIn('мм рт. ст.',r['markdown'])
 def test_meaningful_yes_and_negation(self):
  r=self.clean('Резервирование есть? Да. Ответ — да. Это не не исправность.')
  self.assertIn('есть? Да.',r['markdown']);self.assertIn('Ответ — да.',r['markdown'])
  self.assertIn('не исправность',r['markdown'])
 def test_yes_particles_and_ambiguous_answer(self):
  r=self.clean('Да, система работает. Ввод исправен, да.')
  self.assertIn('Да, система работает.',r['markdown']);self.assertIn('Ввод исправен.',r['markdown'])
  self.assertTrue(any(x['text']=='Да' for x in r['review']))
 def test_comparative_koroche_and_discourse_marker(self):
  r=self.clean('Короче, нужен резерв. Кабель короче прежнего. Короче кабель не берите.')
  self.assertIn('Нужен резерв.',r['markdown']);self.assertIn('Кабель короче прежнего.',r['markdown'])
  self.assertIn('Короче кабель',r['markdown']);self.assertTrue(any(x['text']=='Короче' for x in r['review']))
 def test_content_words_retained_for_review(self):
  r=self.clean('Там установлен сервер. Это значит отказ. У нас сейчас два ввода. Допустим, один отключился.')
  for term in ('Там','значит','У нас сейчас','Допустим'):
   self.assertIn(term,r['markdown']);self.assertTrue(any(x['text'].lower()==term.lower() for x in r['review']))
  self.assertFalse(r['audio_review_completed'])
 def test_conditional_kak_by_and_quotes(self):
  r=self.clean('Как бы не отказал генератор. Слово «ну» здесь процитировано. Мы, как бы, закончили.')
  self.assertIn('Как бы не отказал',r['markdown']);self.assertIn('«ну»',r['markdown']);self.assertIn('Мы закончили.',r['markdown'])
  self.assertGreaterEqual(r['review_count'],2)
 def test_exact_duplicate_keeps_last_and_semantic_difference_flagged(self):
  r=edit([(2,5,'Это резервный ввод.'),(8,12,'Это резервный ввод.'),(12,15,'Система имеет 2 ввода.'),(16,20,'Система имеет 3 ввода.')])
  self.assertEqual(r['markdown'].count('Это резервный ввод.'),1)
  removal=next(e for e in r['removals'] if e['kind']=='duplicate_phrase');self.assertEqual(removal['audio_start'],2)
  self.assertIn('2 ввода',r['markdown']);self.assertIn('3 ввода',r['markdown'])
  self.assertTrue(any(e['kind']=='semantic_repeat' for e in r['review']))
 def test_abbreviations_and_intentional_emphasis(self):
  r=self.clean('Используется т. е. резервирование. Это очень очень важно.')
  self.assertIn('т. е.',r['markdown']);self.assertIn('очень очень',r['markdown'])
  self.assertTrue(any(e['kind']=='word_repeat' for e in r['review']))
 def test_numeric_repetition_not_deleted_without_review(self):
  r=self.clean('Размер 10 10 мм. Обозначение UPS UPS остаётся.')
  self.assertIn('10 10 мм',r['markdown']);self.assertIn('UPS UPS',r['markdown'])
  self.assertGreaterEqual(sum(e['kind']=='word_repeat' for e in r['review']),2)
 def test_stutter_with_comma_and_suspicious_script(self):
  r=self.clean('Система, система работает. เฮ')
  self.assertEqual(r['markdown'].count('Система'),1)
  self.assertTrue(any(e['kind']=='recognition_artifact' for e in r['review']))
  self.assertIn('เฮ',r['markdown'])
 def test_punctuation_after_removed_parentheticals(self):
  r=self.clean('Что влияет на рост? Ну, тут сложно. Это конец, так сказать, нашей передачи. Она грустная. Ну, без этого никак.')
  self.assertIn('рост? Тут сложно.',r['markdown'])
  self.assertIn('Это конец нашей передачи.',r['markdown'])
  self.assertIn('Она грустная. Без этого никак.',r['markdown'])
  self.assertNotRegex(r['markdown'],r'[.!?],|,\s*,|\x00')
 def test_artifacts_and_idempotent_regeneration(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder);raw=[[20,25,'Ну, там расположен сервер, да.']];(p/'segments.json').write_text(json.dumps(raw,ensure_ascii=False))
   stages=[];result=ensure_clean(p,'Сервер','https://youtube.com/watch?v=sample',stages.append)
   md=(p/'transcript.clean.md').read_text();self.assertIn('Там расположен сервер.',md)
   self.assertNotRegex(md,r'\[\d+:|-->')
   self.assertIn('Сверить с аудио',(p/'transcript.review.md').read_text())
   self.assertGreater(result['review_count'],0);self.assertEqual(len(stages),2)
   before=(p/'transcript.clean.md').stat().st_mtime_ns;ensure_clean(p,'Сервер','https://youtube.com/watch?v=sample')
   self.assertEqual((p/'transcript.clean.md').stat().st_mtime_ns,before)
   audit=json.loads((p/'cleanup.json').read_text());self.assertEqual(audit['version'],VERSION)
   self.assertEqual(audit['review'][0]['audio_start'],20)
if __name__=='__main__':
 r=unittest.main(exit=False);print('ALL OK' if r.result.wasSuccessful() else 'FAIL');sys.exit(not r.result.wasSuccessful())
