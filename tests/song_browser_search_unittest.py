"""Free provider normalization and process contract. No external browser/network."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch,Mock
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.browser_search import normalize_hit,search_url,search


class BrowserSearchTests(unittest.TestCase):
    def test_two_free_engines_and_escaped_query(self):
        self.assertTrue(search_url('yandex','песня & текст').startswith('https://yandex.ru/search/?text='))
        self.assertIn('%26',search_url('google','песня & текст'))

    def test_redirects_normalized_and_search_navigation_excluded(self):
        hit=normalize_hit({'href':'/url?q=https%3A%2F%2Fexample.org%2Flyrics%23top','title':'Текст песни'},'google')
        self.assertEqual(hit['url'],'https://example.org/lyrics')
        for url in ['https://yandex.ru/search/?text=foo','https://accounts.google.com/','javascript:alert(1)','file:///etc/passwd']:
            self.assertIsNone(normalize_hit({'href':url,'title':'Текст'},'yandex'))

    def test_isolated_worker_has_timeout_and_no_paid_keys(self):
        process=Mock(returncode=0);process.communicate.return_value=('{"hits": [], "engines": []}','')
        with patch('songs.browser_search.Path.is_file',return_value=True),patch('songs.browser_search.subprocess.Popen',return_value=process) as run:
            self.assertEqual(search('test')['hits'],[])
            args,kwargs=run.call_args
            self.assertIn('song-search',args[0][0]);self.assertTrue(kwargs['start_new_session'])
            process.communicate.assert_called_once_with('{"query": "test"}',timeout=30)


if __name__=='__main__':unittest.main()
