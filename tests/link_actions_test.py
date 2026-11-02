"""Link routing, policy and actual yt-dlp/ffmpeg downloads from a local fixture."""
import functools
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'core'))
import link_actions as links
from link_download import download, run


class LinksTest(unittest.TestCase):
    def engine(self, policy=(True,False)):
        task=SimpleNamespace(public=lambda:dict(id='task123',status='running'))
        return SimpleNamespace(toolbox=SimpleNamespace(policy=Mock(return_value=policy)),
            tasks=SimpleNamespace(create=Mock(return_value=task),create_transcription=Mock(return_value=task),
                                  create_link_download=Mock(return_value=task)),_say_quick=Mock())

    def test_description_is_local_and_plain_links_only(self):
        self.assertEqual(links.urls_only('https://example.org/\nhttps://example.org/'),['https://example.org/'])
        for text in ['расскажи https://example.org','javascript:alert(1)','https://user:secret@example.org/','https://x:bad/','https://[bad/']:
            self.assertFalse(links.urls_only(text))
        e=self.engine()
        self.assertTrue(links.request(e,'https://example.org'))
        self.assertEqual(e._say_quick.call_args.kwargs['sources'][0]['kind'],'link_actions')
        e.tasks.create.assert_not_called()
        self.assertFalse(links.request(e,'Проанализируй https://example.org'))

    def test_routing_scope_and_media(self):
        e=self.engine();url='https://example.org/article'
        for action in ['seo-page','seo-site','text']:
            self.assertEqual(links.start(e,dict(urls=[url],action=action))['id'],'task123')
            title,goal=e.tasks.create.call_args.args
            self.assertIn(url,goal)
            self.assertIn('seo-web-audit' if action.startswith('seo-') else 'link-text',goal)
        links.start(e,dict(urls=['https://youtu.be/fixture'],action='text'))
        e.tasks.create_transcription.assert_called_once()
        links.start(e,dict(urls=['https://youtu.be/fixture'],action='audio'))
        e.tasks.create_link_download.assert_called_once_with(['https://youtu.be/fixture'],'audio')
        # Unknown video host can be explicitly identified by the user.
        links.start(e,dict(urls=[url],action='video',media=True))
        with self.assertRaises(ValueError):links.start(e,dict(urls=[url],action='audio'))

    def test_policy_does_not_start_background_work(self):
        for policy in [(False,False),(True,True)]:
            e=self.engine(policy)
            with self.assertRaises(ValueError):links.start(e,dict(urls=['https://example.org'],action='seo-site'))
            e.tasks.create.assert_not_called()

    def test_playlist_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as folder, patch('yt_dlp.YoutubeDL') as factory:
            loader=factory.return_value.__enter__.return_value
            loader.extract_info.return_value={'_type':'playlist'}
            with self.assertRaises(ValueError):download('https://youtu.be/fixture',Path(folder),'video',threading.Event(),lambda _:None)
            self.assertEqual(loader.extract_info.call_args.kwargs,{'download':False})

    def test_worker_continues_on_error_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'outputs').mkdir()
            t=SimpleNamespace(dir=root,data=dict(link_urls=['https://x/one','https://x/two'],link_action='video'),
                              cancel=threading.Event(),journal=Mock(),save=Mock())
            def fixture(url,directory,*_):
                if url.endswith('one'):raise ValueError('fixture blocked')
                directory.mkdir(parents=True);p=directory/'media.mp4';p.write_bytes(b'video');return p,'Fixture'
            m=SimpleNamespace(_beat=Mock(),_emit=Mock(),_work=Mock())
            with patch('link_download.download',side_effect=fixture):run(m,t)
            self.assertEqual(t.data['status'],'failed')
            rows=json.loads((root/'outputs/media-results.json').read_text())
            self.assertEqual([r['status'] for r in rows],['error','ok']);self.assertEqual(m._work.call_count,2)
            self.assertEqual(m._work.call_args.args[2]['path'],'outputs/media-results.json')

    def test_real_local_video_and_audio(self):
        from eyes import ffmpeg_path
        ff=ffmpeg_path()
        self.assertTrue(ff,'Release test needs the bundled ffmpeg')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            subprocess.run([ff,'-v','error','-f','lavfi','-i','color=c=blue:s=64x64:r=10',
                '-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','1','-c:v','mpeg4','-c:a','aac',str(root/'fixture.mp4')],check=True)
            class Quiet(SimpleHTTPRequestHandler):
                def log_message(self,*_):pass
            server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=folder))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url=f'http://127.0.0.1:{server.server_port}/fixture.mp4'
                video,_=download(url,root/'video','video',threading.Event(),lambda _:None)
                self.assertEqual(video.read_bytes(),(root/'fixture.mp4').read_bytes())
                audio,_=download(url,root/'audio','audio',threading.Event(),lambda _:None)
                self.assertEqual(audio.suffix,'.m4a')
                # Real decoding ensures this is an audio stream, not a renamed HTML/video file.
                decoded=subprocess.run([ff,'-v','error','-i',str(audio),'-map','0:a:0','-f','null','-'],capture_output=True)
                self.assertEqual(decoded.returncode,0,decoded.stderr)
            finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
