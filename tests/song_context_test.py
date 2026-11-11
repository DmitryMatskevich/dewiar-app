"""Local conversation routing with an isolated library; no real player/network."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from engine_music import MusicMixin
from songs.store import SongStore
import tools

class Engine(MusicMixin):
    def __init__(self,root):
        self.song_store=SongStore(root)
        self.chatlog=SimpleNamespace(active='local-chat')
        self.s=SimpleNamespace(get=lambda *args:None)
        self.toolbox=Mock();self.toolbox.policy.return_value=(True,False)
        self.emit=Mock();self._say_quick=Mock()
    def add(self,title,artist,phrases=()):
        row=self.song_store.create(title,artist)
        return self.song_store.patch(row['song_id'],{'audio':{'path':'fixture.wav'},'phrases':[{'text':s} for s in phrases]},row['revision'])

class SongContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.e=Engine(Path(self.tmp.name)/'songs')
        self.a=self.e.add('За хлеб и воду','Владимир Высоцкий',['За высоким забором тают звезды','И туман укрывает старый причал'])
        self.b=self.e.add('Мой первый срок','Владимир Высоцкий',['Незнакомый город за окном'])
    def test_list_short_title_then_play_and_model_tool_mistake(self):
        e=self.e
        self.assertTrue(e._pc_cmd('Открой песню Высоцкого'))
        e.emit.assert_not_called()
        self.assertTrue(e._pc_cmd('хлеб и воду'))
        self.assertEqual(e.emit.call_args.kwargs['songs'][0]['song_id'],self.a['song_id'])
        for command in ['играй','А зачем лишний раз нажимать? Сама просто включи плей. Уже же сказано — играй.']:
            self.assertTrue(e._pc_cmd(command))
            self.assertEqual(e.emit.call_args.kwargs['action'],'play')
        ctx={'local_music_guard':e._guard_local_music}
        with patch.object(tools,'launch',side_effect=AssertionError('external launch')):
            tools.t_yandex_music({},ctx)
            tools.t_play_music({},ctx)
            tools.t_media({'action':'play'},ctx)
        self.assertEqual(e.emit.call_args.kwargs['songs'][0]['song_id'],self.a['song_id'])
    def test_automatic_lyrics_and_typo_lookup(self):
        for query in ['тают звезды','по словам: туман старый причал','хлеп и воду']:
            with self.subTest(query=query):
                result=self.e._song_library_action({'action':'open','query':query})
                self.assertIn('Открываю',result)
                self.assertEqual(self.e.emit.call_args.kwargs['songs'][0]['song_id'],self.a['song_id'])
    def test_ambiguity_does_not_guess_and_context_does_not_cross_chats(self):
        e=self.e;e._pc_cmd('включи песню Высоцкого')
        e._pc_cmd('играй');e.emit.assert_not_called()
        e._pc_cmd('вторую');self.assertEqual(e.emit.call_args.kwargs['action'],'play')
        e.chatlog.active='other-chat'
        self.assertIsNone(e._guard_local_music('search',{}))

    def test_negative_play_and_unrelated_reply_are_not_consumed(self):
        e=self.e;e._pc_cmd('открой песню Высоцкого')
        self.assertFalse(e._local_song_followup('спасибо'))
        self.assertFalse(e._local_song_followup('не играй'))
        e.emit.assert_not_called()

    def test_source_change_requires_request_not_complaint(self):
        for text in ('Опять пошел в Яндекс Музыку','не открывай Яндекс, играй локально'):
            self.assertFalse(self.e._explicit_external_music(text))
        for text in ('Найди Высоцкого в Яндекс Музыке','Яндекс','включи радио'):
            self.assertTrue(self.e._explicit_external_music(text))

if __name__=='__main__':unittest.main()
