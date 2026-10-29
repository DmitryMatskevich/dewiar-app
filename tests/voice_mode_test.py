"""Mac voice activation: permission gate, persisted choice and single hearing startup."""
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'core'))
from engine_commands import CommandsMixin


class VoiceMode(unittest.TestCase):
    def make_engine(self):
        eng = SimpleNamespace(_voice_enable_lock=threading.Lock(), server=True, null_player=True, player=object(), toolbox=SimpleNamespace(hooks={'server':True}),
                              status={}, s=SimpleNamespace(get=Mock(return_value='off'), update=Mock(), snapshot=Mock(return_value={})),
                              _start_hearing=Mock(), emit=Mock())
        return eng

    def test_permission_required_before_any_audio_start(self):
        eng=self.make_engine()
        with patch('macsys.microphone_permission',return_value={'granted':False}), patch('engine_commands.threading.Thread') as thread:
            with self.assertRaisesRegex(ValueError,'Разрешите'):
                CommandsMixin.command(eng,'audio_enable',{})
            self.assertTrue(eng.server); thread.assert_not_called(); eng.s.update.assert_not_called()

    def test_enable_and_repeated_click_start_hearing_once(self):
        eng=self.make_engine()
        with patch('macsys.microphone_permission',return_value={'granted':True}), patch('engine_commands.threading.Thread') as thread:
            result=CommandsMixin.command(eng,'audio_enable',{})
            CommandsMixin.command(eng,'audio_enable',{})
            self.assertEqual(result,{'audio_mode':'voice'})
            self.assertFalse(eng.server);self.assertFalse(eng.null_player)
            self.assertFalse(eng.toolbox.hooks['server'])
            thread.assert_called_once();thread.return_value.start.assert_called_once()
            self.assertEqual(eng.s.update.call_args.args[0]['audio'],{'voice_enabled':True})
            self.assertEqual(eng.s.update.call_args.args[0]['tts'],{'engine':'espeak','text_only':False})

    def test_existing_voice_engine_is_preserved(self):
        eng=self.make_engine();eng.s.get.return_value='piper'
        with patch('macsys.microphone_permission',return_value={'granted':True}),patch('engine_commands.threading.Thread'):
            CommandsMixin.command(eng,'audio_enable',{})
        self.assertNotIn('tts',eng.s.update.call_args.args[0])

    def test_coreaudio_uses_device_rate_and_resamples_voice(self):
        import audio_io
        from unittest.mock import MagicMock
        stream = MagicMock(spec=["start", "write", "abort", "close"])
        with patch('sounddevice.query_devices', return_value={'default_samplerate':48000}), patch('sounddevice.RawOutputStream', return_value=stream):
            player=audio_io.Player()
            player.start(22050)
            player._lead=0
            player.write(b'\x00\x00'*882)
            self.assertEqual(player._output_rate,48000)
            data=stream.write.call_args.args[0]
            self.assertGreater(len(data),882*2)
            player.stop()

    def test_native_mac_speech_produces_pcm(self):
        from tts import EspeakTTS
        rate, pcm=next(EspeakTTS().synth('Проверка голоса.',{}))
        self.assertEqual(rate,22050);self.assertGreater(len(pcm),10000)


if __name__=='__main__':unittest.main()
