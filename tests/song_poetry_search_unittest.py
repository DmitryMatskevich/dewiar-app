"""Retrieval regressions: query coverage, poem body, blocked providers, page budget."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.reference_fragments import anchors,query_plan
from songs.reference_text import tokens
from songs.text_reference import run,web_search,confirm_text
from songs.sources import _embedded_lyrics


def phrase(text,i):
 return dict(phrase_id=str(i),text=text,review_state='verified',start_ms=i*10000,end_ms=(i+1)*10000)

class PoetrySearchTests(unittest.TestCase):
 def test_plain_poetry_query_reached_for_coherent_phrase_not_only_top_score(self):
  selected=[{'text':'все секреты— Ты встретишь меня, Завернешься в одеяло','score':12},
            {'text':'одеяло, Слыша крик, летящий вдаль, Мы с тобой','score':11},
            {'text':'Где-то между тенью и светом','score':7}]
  plan=query_plan(selected,'Ошибочное название','Ошибочный исполнитель')
  self.assertEqual(plan[1]['query'],'Где-то между тенью и светом стихи')
  self.assertTrue(any('site:stihi.ru' in q['query'] for q in plan))
  self.assertNotIn('Ошибоч',plan[0]['query'])
  self.assertLessEqual(len(plan),8)

 def test_discovery_by_one_phrase_confirmation_by_three_and_cache_not_starving_search(self):
  ps=[phrase(t,i) for i,t in enumerate(['Вечерний поезд медленно уходит за деревню',
    'Тихий ветер согревает окна нашего дома','Ранним утром золотые листья падают на землю'])]
  queries=[]; visited=[]
  context={'source_candidates':[dict(kind='lyrics',url=f'https://example.org/old/{i}') for i in range(10)]}
  def search(q):
   queries.append(q)
   return [{'url':'https://example.org/new-poem'}] if q.endswith(' стихи') else []
  def fetch(url):
   visited.append(url)
   return dict(status='available',extracted_text='\n'.join(p['text'] for p in ps) if url.endswith('new-poem') else 'Не та публикация')
  updated,report=run(ps,context,search=search,fetch=fetch)
  self.assertEqual(updated,ps)
  self.assertEqual(report['state'],'compared')
  self.assertEqual(len(report['references'][0]['text_confirmation']['hits']),3)
  self.assertLessEqual(sum('/old/' in u for u in visited),4)
  self.assertFalse(confirm_text(ps[0]['text'],anchors(ps))['confirmed'])

 def test_poem_parser_excludes_comments_and_navigation(self):
  body='<div class="maintext"><h1>Автор</h1><div class="text">Первая строка<br>Вторая <b>строка</b></div><div>Рецензии: посторонние строки</div></div>'
  for host in ['stihi.ru','proza.ru']:
   self.assertEqual(_embedded_lyrics(body,'https://'+host+'/test'),'Первая строка\nВторая строка')
  self.assertEqual(_embedded_lyrics(body,'https://example.org/test'),'')

 def test_captcha_provider_not_retried_and_not_reported_as_empty(self):
  blocked={}
  with patch('songs.browser_search.search',return_value={'hits':[],'engines':[{'engine':e,'status':'captcha','message':'Проверка в браузере'} for e in ['yandex','google']]}) as browser,patch('songs.text_reference.urlopen',side_effect=OSError('offline')):
   for q in ['первая фраза','другая фраза']:
    with self.assertRaisesRegex(RuntimeError,'Проверка в браузере'):web_search(q,blocked=blocked)
   self.assertEqual(browser.call_count,1)
   self.assertEqual(set(blocked),{'yandex','google'})

if __name__=='__main__':unittest.main()
