"""App transcription: captions, fallback, batch errors, resume, cancellation and task dispatch."""
import json
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import media_transcription as M
import video


class TranscriptionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'audio.wav'
        self.source.write_bytes(b'audio')
        self.calls = []

    def recognize(self, path, language):
        self.calls.append((path, language))
        return [(0, 2.5, 'Русская речь.')], 'gigaam', 'gigaam-v3-e2e-rnnt'

    def batch(self, sources, **kw):
        return M.run_batch(sources, self.root / 'outputs/transcripts', lambda _: self.source,
                           self.recognize, pause=0, **kw)

    def test_full_subtitles_first(self):
        tr = dict(title='Видео', lang='ru', auto=False, segments=[(0, 1.25, 'Начало.'), (1600, 1617, 'Конец.')])
        with patch.object(video, 'transcript', return_value=tr), patch.object(M, '_audio') as audio:
            result = self.batch(['https://youtu.be/test'])[0]
        self.assertEqual(self.calls, [])
        audio.assert_not_called()
        self.assertEqual(result['method'], 'subtitles')
        text = (self.root / 'outputs' / result['text']).read_text()
        self.assertIn('Конец.', text)
        self.assertNotIn('[', text)
        self.assertIn('00:26:57,000', (self.root / 'outputs' / result['srt']).read_text())

    def test_fallback_after_missing_or_failed_captions(self):
        for failure in (video.NoTranscript('нет субтитров'), RuntimeError('subtitle HTTP 403')):
            with patch.object(video, 'transcript', side_effect=failure), patch.object(M, '_audio', return_value=(self.source, {})):
                result = self.batch(['https://youtu.be/test'], force=True)[0]
            self.assertEqual(result['method'], 'gigaam')
            self.assertEqual(result['model'], 'gigaam-v3-e2e-rnnt')
            self.assertEqual(self.calls[-1], (self.source, 'ru'))

    def test_downloader_uses_actual_filename_and_checks_cancellation(self):
        folder = self.root / 'download'
        folder.mkdir()
        audio = folder / 'audio.m4a'
        cancel = threading.Event()
        class YDL:
            def __init__(self, opts):
                self.opts = opts
                assert opts['noplaylist'] and opts['format'] == 'bestaudio/best'
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, url, download):
                assert download
                self.opts['progress_hooks'][0]({})
                audio.write_bytes(b'audio bytes')
                return {'title': 'Название, а не ID из URL', 'requested_downloads': [{'filepath': str(audio)}]}
            def prepare_filename(self, info): return str(folder / 'wrong-name.webm')
        with patch.dict(sys.modules, {'yt_dlp': SimpleNamespace(YoutubeDL=YDL)}):
            result, _ = M._audio('https://youtu.be/test', folder, cancel)
            self.assertEqual(result, audio)
            cancel.set()
            with self.assertRaises(M.Cancelled):
                M._audio('https://youtu.be/test', folder, cancel)

    def test_error_continuation_unique_outputs_and_resume(self):
        def tr(url):
            if 'bad' in url:
                raise RuntimeError('429 Too Many Requests')
            return dict(title='Одинаковое имя', lang='ru', auto=True, segments=[(0, 1, url)])
        urls = ['https://youtu.be/bad', 'https://youtu.be/one', 'https://youtu.be/two', 'https://youtu.be/one']
        with patch.object(video, 'transcript', side_effect=tr), patch.object(M, '_audio') as audio:
            results = self.batch(urls)
        self.assertEqual([r['status'] for r in results], ['error', 'ok', 'ok'])
        self.assertNotEqual(results[1]['text'], results[2]['text'])
        audio.assert_not_called()
        with patch.object(video, 'transcript', side_effect=AssertionError('must use saved text')):
            self.assertTrue(self.batch([urls[1]])[0]['cached'])

    def test_cancellation_does_not_publish_partial_asr(self):
        cancel = threading.Event()
        def recognize(*args):
            cancel.set()
            return [(0, 1, 'Частичный текст')], 'gigaam', 'test'
        with self.assertRaises(M.Cancelled):
            M.run_batch([str(self.source)], self.root / 'outputs/transcripts', lambda _: self.source,
                        recognize, cancel, pause=0)
        report = json.loads((self.root / 'outputs/transcripts/batch-results.json').read_text())
        self.assertEqual(report[0]['status'], 'cancelled')
        self.assertEqual(list((self.root / 'outputs').rglob('transcript.txt')), [])

    def test_sidecar_empty_asr_and_file_change(self):
        self.source.with_suffix('.srt').write_text('1\n00:00:00,000 --> 00:00:01,500\nТекст.\n')
        result = self.batch([str(self.source)])[0]
        self.assertEqual(result['method'], 'subtitles')
        self.assertEqual(self.calls, [])
        self.source.with_suffix('.srt').unlink()
        self.source.write_bytes(b'changed audio')
        self.assertEqual(self.batch([str(self.source)])[0]['method'], 'gigaam')
        with patch.object(self, 'recognize', return_value=([], 'gigaam', 'test')):
            self.assertEqual(self.batch([str(self.source)], force=True)[0]['status'], 'error')

    def test_caption_times_and_rolling_overlap(self):
        vtt = 'WEBVTT\n\n00:00:01.250 --> 00:00:03.500\nРаз два\n\n00:00:02.500 --> 00:00:04.750\nРаз два три\n'
        self.assertEqual(video.caption_segments(vtt, 'vtt'), [(1.25, 3.5, 'Раз два'), (2.5, 4.75, 'три')])
        self.assertEqual(video.caption_segments('<timedtext><body><p t="1250" d="500"><s>Речь &amp; текст</s></p></body></timedtext>', 'srv3'), [(1.25, 1.75, 'Речь & текст')])

    def test_app_tool_accepts_url_list_file_and_shows_results(self):
        from tasks import TaskManager, WORK_TOOLS
        self.assertIn('urls_file', WORK_TOOLS['transcribe'][1]['properties'])
        (self.root / 'urls.txt').write_text('# test\nhttps://youtu.be/one\n\n')
        task = SimpleNamespace(dir=self.root, data={}, cancel=threading.Event(), save=lambda: None, journal=lambda message: None)
        manager = TaskManager.__new__(TaskManager)
        manager.host = SimpleNamespace(workspace=SimpleNamespace(root=self.root))
        manager._emit = lambda task: None
        tr = dict(title='Тест', lang='ru', auto=False, segments=[(0, 1.5, 'Текст ролика.')])
        with patch.object(video, 'transcript', return_value=tr):
            reply = manager._work(task, 'transcribe', {'urls_file': 'urls.txt'})
        self.assertIn('Обработано: 1 из 1', reply)
        self.assertEqual(len(task.data['outputs']), 4)
        for output in task.data['outputs']:
            self.assertTrue((self.root / output['path']).is_file())

    def test_local_permission_error_is_reported_before_recognition(self):
        from tasks import TaskManager
        task = SimpleNamespace(dir=self.root, data={}, cancel=threading.Event(), save=lambda:None, journal=lambda message:None)
        manager = TaskManager.__new__(TaskManager)
        manager.host = SimpleNamespace(workspace=SimpleNamespace(root=self.root))
        manager._emit = lambda task:None
        original = Path.open
        def denied(path, *args, **kwargs):
            if path == self.source:
                raise PermissionError('Operation not permitted')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', denied):
            reply = manager._work(task, 'transcribe', {'path':str(self.source)})
        self.assertIn('Нет доступа к файлу', reply)
        self.assertIn('кнопку добавления файла', reply)
        self.assertEqual(task.data.get('outputs',[]),[])

    def test_shared_result_cache_across_tasks_and_youtube_aliases(self):
        cache = self.root/'.cache'
        tr = dict(title='Ролик',lang='ru',auto=False,segments=[(0,1,'Готовый текст')])
        with patch.object(video,'transcript',return_value=tr) as fetch:
            one = M.run_batch(['https://youtu.be/abc'],self.root/'one/outputs/transcripts',lambda _:self.source,self.recognize,pause=0,cache=cache)
            two = M.run_batch(['https://www.youtube.com/watch?v=abc&t=10'],self.root/'two/outputs/transcripts',lambda _:self.source,self.recognize,pause=0,cache=cache)
        self.assertEqual(fetch.call_count,1)
        self.assertTrue(two[0]['cached'])
        self.assertEqual((self.root/'one/outputs'/one[0]['text']).read_text(),(self.root/'two/outputs'/two[0]['text']).read_text())

    def test_failed_asr_retry_uses_downloaded_audio_without_network(self):
        cache = self.root/'.cache';url='https://youtu.be/failed-asr'
        def downloaded(source,directory,cancel):
            path=directory/'audio.webm';path.write_bytes(b'saved audio')
            M._json(directory/'audio-cache.json',dict(source_key=M.source_key(source),file=path.name,bytes=path.stat().st_size,info={'title':'Речь','language':'ru'}))
            return path,{'title':'Речь','language':'ru'}
        with patch.object(video,'transcript',side_effect=video.NoTranscript('нет субтитров')),patch.object(M,'_audio',side_effect=downloaded) as download:
            failed=M.run_batch([url],self.root/'one/outputs/transcripts',lambda _:self.source,lambda *a:(_ for _ in ()).throw(RuntimeError('ASR interrupted')),pause=0,cache=cache)
        self.assertEqual(failed[0]['status'],'error');self.assertEqual(download.call_count,1)
        with patch.object(video,'transcript',side_effect=AssertionError('Network must not be used')) as network:
            result=M.run_batch([url],self.root/'two/outputs/transcripts',lambda _:self.source,self.recognize,pause=0,cache=cache)
        self.assertEqual(result[0]['status'],'ok');network.assert_not_called()
        self.assertEqual(self.calls[-1][0].read_bytes(),b'saved audio')

    def test_overlapping_batches_fetch_same_source_once(self):
        cache=self.root/'.cache';entered=threading.Event();release=threading.Event();results=[]
        def fetch(url):
            entered.set();release.wait(3)
            return dict(title='Речь',lang='ru',auto=False,segments=[(0,1,'Текст')])
        def work(name):
            results.append(M.run_batch(['https://youtu.be/same'],self.root/name/'outputs/transcripts',lambda _:self.source,self.recognize,pause=0,cache=cache))
        with patch.object(video,'transcript',side_effect=fetch) as network:
            a=threading.Thread(target=work,args=('one',));b=threading.Thread(target=work,args=('two',))
            a.start();self.assertTrue(entered.wait(2));b.start();release.set();a.join(4);b.join(4)
        self.assertEqual(network.call_count,1);self.assertEqual(len(results),2)
        self.assertEqual(sum(bool(r[0].get('cached')) for r in results),1)

    def test_shared_batch_error_continues_and_completed_source_skipped(self):
        def fetch(url):
            if 'bad' in url:raise RuntimeError('429 Too Many Requests')
            return dict(title='Одинаковое имя',lang='ru',auto=False,segments=[(0,1,url)])
        urls=['https://youtu.be/one','https://youtu.be/bad','https://youtube.com/watch?v=one','https://youtu.be/two']
        with patch.object(video,'transcript',side_effect=fetch):
            result=M.run_batch(urls,self.root/'out/transcripts',lambda _:self.source,self.recognize,pause=0,cache=self.root/'.cache')
        self.assertEqual([r['status'] for r in result],['ok','error','ok'])

    def test_previous_task_results_seed_new_shared_cache(self):
        task_dir=self.root/'old-task'
        tr=dict(title='Уже обработано',lang='ru',auto=False,segments=[(0,1,'Текст')])
        with patch.object(video,'transcript',return_value=tr):
            M.run_batch(['https://youtu.be/previous'],task_dir/'outputs/transcripts',lambda _:self.source,self.recognize,pause=0)
        cache=self.root/'.cache';M.seed_cache(cache,task_dir)
        with patch.object(video,'transcript',side_effect=AssertionError('Must reuse old task')) as fetch:
            result=M.run_batch(['https://youtube.com/watch?v=previous'],self.root/'new-task/outputs/transcripts',lambda _:self.source,self.recognize,pause=0,cache=cache)
        self.assertTrue(result[0]['cached']);fetch.assert_not_called()

    def test_batch_api_reuses_uploaded_file_on_disk(self):
        import base64
        from engine_commands import CommandsMixin
        from unittest.mock import Mock
        task=SimpleNamespace(id='abcd1234',public=lambda:dict(id='abcd1234',status='running'))
        manager=SimpleNamespace(tasks={},create_transcription=Mock(return_value=task))
        host=SimpleNamespace(tasks=manager,workspace=SimpleNamespace(ensure=lambda:self.root))
        args=dict(sources=[],files=[dict(name='clip.wav',b64=base64.b64encode(b'audio bytes').decode())])
        CommandsMixin.command(host,'media_transcribe',args)
        first=Path(manager.create_transcription.call_args.args[0][0]);mtime=first.stat().st_mtime_ns
        CommandsMixin.command(host,'media_transcribe',args)
        second=Path(manager.create_transcription.call_args.args[0][0])
        self.assertEqual(first,second);self.assertEqual(second.stat().st_mtime_ns,mtime)
        self.assertEqual(first.read_bytes(),b'audio bytes')

    def test_markdown_failure_retries_from_raw_without_download_or_asr(self):
        url='https://youtu.be/raw-ready'
        with patch.object(video,'transcript',return_value=dict(title='Тест',lang='ru',auto=False,segments=[(0,1,'Ну, система работает.')])):
            with patch.object(M,'ensure_clean',side_effect=RuntimeError('editor interrupted')):
                failed=self.batch([url])
        self.assertEqual(failed[0]['status'],'error')
        self.assertTrue(failed[0]['extraction_complete'])
        with patch.object(video,'transcript',side_effect=AssertionError('must not download')) as fetch:
            result=self.batch([url])
        self.assertEqual(result[0]['status'],'ok');self.assertTrue(result[0]['cached']);fetch.assert_not_called()
        self.assertTrue((self.root/'outputs'/result[0]['markdown']).is_file())

    def test_editor_failure_still_publishes_completed_raw_stage(self):
        from tasks import TaskManager
        task=SimpleNamespace(dir=self.root,data={'workflow':'media-transcription'},cancel=threading.Event(),save=lambda:None,journal=lambda text:None)
        manager=TaskManager.__new__(TaskManager);manager.host=SimpleNamespace(workspace=SimpleNamespace(root=self.root));manager._emit=lambda t:None
        tr=dict(title='Речь',lang='ru',auto=False,segments=[(0,1,'Ну, система работает.')])
        with patch.object(video,'transcript',return_value=tr),patch.object(M,'ensure_clean',side_effect=RuntimeError('editor failed')):
            reply=manager._work(task,'transcribe',{'path':'https://youtu.be/raw-complete'})
        self.assertIn('Исходная расшифровка сохранена',reply)
        self.assertEqual(len(task.data['outputs']),2)
        for artifact in task.data['outputs']:
            self.assertTrue((self.root/artifact['path']).is_file())


if __name__ == '__main__':
    result = unittest.main(exit=False)
    if result.result.wasSuccessful():
        print('ALL OK')
    sys.exit(not result.result.wasSuccessful())
