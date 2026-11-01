"""Long voice input/output: complete API responses, bounded TTS pieces, no aggregate cap."""
import collections
import json
import queue
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import llm
from engine_audio import AudioMixin, FRAME
from engine_remote import RemoteMixin
from settings import DEFAULTS


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.seen.append(body)
        index = len(self.server.seen) - 1
        if self.server.fail and index:
            self.send_response(503); self.end_headers(); return
        text = self.server.parts[min(index, len(self.server.parts)-1)]
        reason = 'length' if index < len(self.server.parts)-1 or self.server.repeat else 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        if self.path == '/api/chat':
            events = [{'message': {'content': text}}, {'done': True, 'done_reason': reason}]
        elif self.path == '/v1/messages':
            events = [{'type': 'content_block_delta', 'delta': {'text': text}},
                      {'type': 'message_delta', 'delta': {'stop_reason': 'max_tokens' if reason == 'length' else 'end_turn'}}]
        else:
            events = [{'choices': [{'delta': {'content': text}}]}, {'choices': [{'delta': {}, 'finish_reason': reason}]}]
        if self.server.reasoning:
            events = [{'choices':[{'delta':{'reasoning_content':'Рассуждаю дальше'}}]}] * 5 + events
        if self.server.missing_end:
            events = events[:1]
        if self.server.tool:
            events = [{'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'broken',
                'function': {'name': 'delete_file', 'arguments': '{"path":'}}]}}]},
                {'choices': [{'delta': {}, 'finish_reason': 'length'}]}]
        for event in events:
            self.wfile.write(('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode())


class LongVoice(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.seen, self.server.parts = [], ['Первая часть. ', 'Вторая часть. ', 'Последний маркер.']
        self.server.fail = self.server.repeat = False
        self.server.missing_end = self.server.tool = False
        self.server.reasoning = False
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.profile = {'kind': 'openai', 'model': 'fixture', 'base_url': f'http://127.0.0.1:{self.server.server_port}/v1',
                        'max_tokens': 128, 'think': 'off', '_continue_response': True}

    def test_all_api_formats_continue_to_natural_stop(self):
        for kind in ['openai', 'ollama', 'anthropic']:
            with self.subTest(kind=kind):
                self.server.seen = []
                profile = dict(self.profile, kind=kind)
                result = list(llm.stream_events(profile, 'Fixture', [{'role': 'user', 'content': 'Длинный ответ'}]))
                self.assertEqual(''.join(t for k,t in result if k == 'text'), ''.join(self.server.parts))
                self.assertEqual(len(self.server.seen), 3)
                self.assertTrue(all(k == 'text' for k,t in result), 'Private finish metadata leaked')
                histories = [r['messages'] for r in self.server.seen]
                self.assertIn(self.server.parts[0] + self.server.parts[1], str(histories[-1]))

    def test_explicit_disabled_continuation_keeps_per_request_limit(self):
        result = list(llm.stream_events(dict(self.profile, _continue_response=False), '', []))
        self.assertEqual(result, [('text', self.server.parts[0])])
        self.assertEqual(len(self.server.seen), 1)

    def test_broken_stream_is_not_reported_as_a_complete_answer(self):
        self.server.missing_end = True
        result = []
        with self.assertRaisesRegex(llm.LLMError, 'без подтверждения'):
            for event in llm.stream_events(self.profile, '', []):
                result.append(event)
        self.assertEqual(result, [('text', self.server.parts[0])])

    def test_partial_tool_call_is_never_executed(self):
        self.server.tool = True
        result = []
        with self.assertRaises(llm.LLMError):
            for event in llm.stream_events(self.profile, '', [], tools=[{'name': 'delete_file', 'description': '', 'parameters': {'type':'object'}}]):
                result.append(event)
        self.assertFalse(any(k == 'tools' for k,v in result))

    def test_cancel_before_next_request(self):
        cancel = threading.Event()
        stream = llm.stream_events(self.profile, '', [], cancel)
        self.assertEqual(next(stream), ('text', self.server.parts[0]))
        cancel.set()
        self.assertEqual(list(stream), [])
        self.assertEqual(len(self.server.seen), 1)

    def test_reasoning_progress_does_not_hit_total_routing_timeout(self):
        self.server.parts = ['Готово.']
        self.server.reasoning = True
        clock = [100.0]
        feed = llm._Collector.feed_line
        def advance(collector, line):
            clock[0] += 9
            return feed(collector, line)
        with patch.object(llm, 'time', SimpleNamespace(monotonic=lambda:clock[0])), patch.object(llm._Collector, 'feed_line', advance):
            result = list(llm.stream_events(dict(self.profile, timeout=10, _routing_deadline=110), '', []))
        self.assertEqual(''.join(t for k,t in result), 'Готово.')
        self.assertGreater(clock[0], 150)

    def test_failure_preserves_already_delivered_text(self):
        self.server.fail = True
        result = []
        with self.assertRaises(llm.LLMError):
            for event in llm.stream_events(self.profile, '', []):
                result.append(event)
        self.assertEqual(''.join(t for k,t in result if k == 'text'), self.server.parts[0])

    def test_no_infinite_loop_when_model_repeats_truncated_response(self):
        self.server.parts = ['Одна и та же фраза']
        self.server.repeat = True
        with self.assertRaisesRegex(llm.LLMError, 'не смогла продолжить'):
            list(llm.stream_events(self.profile, '', []))
        self.assertEqual(len(self.server.seen), 2)

    def test_long_unpunctuated_text_is_not_lost(self):
        text = ' '.join(f'слово{i}' for i in range(5000)) + ' ПОСЛЕДНИЙМАРКЕР'
        parts = list(llm.speech_chunks(text))
        self.assertEqual(''.join(parts), text)
        self.assertLessEqual(max(map(len, parts)), 400)
        split = llm.SentenceSplitter()
        spoken = []
        for start in range(0, len(text), 23):
            spoken += split.feed(text[start:start+23])
        spoken += split.flush()
        self.assertEqual(' '.join(spoken).split(), text.split())
        self.assertLessEqual(max(map(len, spoken)), 400)

    def test_playback_synthesizes_all_text_even_without_sentence_splitter(self):
        text = ('Очень длинный ответ без запятых ' * 2000).strip() + ' последниймаркер'
        spoken, audio = [], []
        def synth(eng, part, voice, speed):
            spoken.append(part)
            yield 16000, b'\x01\x00'
        player = SimpleNamespace(rate=16000, start=lambda rate: None, write=lambda pcm: audio.append(pcm) or True, finish=lambda: None)
        owner = SimpleNamespace(s=SimpleNamespace(get=lambda *k: None), _output_muted=lambda: False, _voice_cfg=lambda: {},
            _fx_name=lambda voice: 'off', _wait_vosk_tts=lambda voice: None, _engines_for_speech=lambda voice: [object()],
            _synth_mixed=synth, vosk_tts=None, _said=[], player=player, set_state=lambda state: None)
        turn = SimpleNamespace(cancel=threading.Event(), spoken=[])
        sentences = queue.Queue(); sentences.put(text); sentences.put(None)
        RemoteMixin._speak_worker(owner, turn, sentences)
        self.assertEqual(''.join(spoken), llm.speakable(text))
        self.assertTrue(spoken[-1].endswith('последниймаркер'))
        self.assertLessEqual(max(map(len, spoken)), 400)
        self.assertEqual(turn.spoken, [text])

    def test_hold_default_no_ten_minute_cutoff(self):
        owner = SimpleNamespace(server=False, state='idle', follow_up_until=0, _mic_ring=collections.deque([b'x']),
                                set_state=lambda s: None, emit=lambda *a, **k: None)
        AudioMixin.hold_start(owner)
        self.assertEqual(owner.hold['max'], 0)
        AudioMixin.hold_start(owner, max_s=20)
        self.assertEqual(owner.hold['max'], 20)
        self.assertTrue(DEFAULTS['llm']['continue_response'])
        self.assertTrue(DEFAULTS['audio']['follow_long'])


if __name__ == '__main__':
    unittest.main()
