"""No live state: list polling must not parse full archives on every request."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.store import SongStore
from songs.instrumental import InstrumentalManager


class CardCacheTests(unittest.TestCase):
    def test_read_once_and_invalidate_after_atomic_external_write(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SongStore(Path(temp));row=store.create('Первая')
            with patch.object(store,'_read',wraps=store._read) as read:
                store.list(compact=True);store.list(compact=True)
                self.assertEqual(read.call_count,1)
                path=store._path(row['song_id']);row['title']='Вторая'
                other=path.with_suffix('.new');other.write_text(json.dumps(row));other.replace(path)
                self.assertEqual(store.list(compact=True)[0]['title'],'Вторая')
                self.assertEqual(read.call_count,2)
            listed=store.list(compact=True);listed[0]['audio']['path']='mutation'
            self.assertEqual(store.list(compact=True)[0]['audio']['path'],'')
            store.patch(row['song_id'],{'title':'Третья'},row['revision'])
            self.assertEqual(store.list(compact=True)[0]['title'],'Третья')
            path.unlink()
            self.assertEqual(store.list(compact=True),[])

    def test_instrumental_summary_never_reads_full_song(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SongStore(Path(temp));row=store.create('Тест')
            with patch.object(InstrumentalManager,'recover'):
                manager=InstrumentalManager(Path(temp),store,lambda *a,**k:None)
            with patch.object(store,'get',side_effect=AssertionError('Full archive read')):
                self.assertEqual(manager.summary(row['song_id'])['state'],'absent')
                with self.assertRaises(ValueError):manager.summary('../escape')
                with self.assertRaises(ValueError):manager.summary('a'*32)


if __name__=='__main__':unittest.main()
