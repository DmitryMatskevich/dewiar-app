"""Local synthetic media only; FFmpeg is already installed, no model/network use."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
import http.cookiejar
import urllib.request
import urllib.error
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from content.store import ContentStore
from content import video_store as video, video_exchange as exchange
from content import video_preview


class ExchangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='dewiar-test-media-')
        cls.clip = Path(cls.temp.name) / 'fixture.mp4'
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            cls.temp.cleanup()
            raise unittest.SkipTest('Existing ffmpeg/ffprobe required; no installation attempted')
        subprocess.run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=25:d=1',
                        '-f', 'lavfi', '-i', 'sine=frequency=600:duration=1', '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(cls.clip)], check=True, timeout=15)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ContentStore(Path(self.tmp.name) / 'content')
        self.row = video.create(self.store, {'request_key': 'video', 'data': {'title': 'TEST fixture', 'scenes': [
            {'id': 's1', 'title': 'TEST', 'narration': 'TEST', 'visual_prompt': 'Blue fixture', 'duration_us': 1000000}]}})
        self.pack = exchange.handoff(self.store, {'id': self.row['id'], 'revision': 1})

    def start(self, key='import', **kw):
        return exchange.start_import(self.store, {'request_key': key, 'path': str(self.clip), 'handoff_hash': self.pack['hash']}, **kw)['id']

    def finish(self, ident):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with exchange._lock:
                active = exchange._active.get((str(self.store.path), ident))
            if not active:
                return exchange.get_import(self.store, ident)
            active[1].join(.1)
        self.fail('Import worker did not finish')

    def test_handoff_deterministic_and_every_entry_hash(self):
        self.assertEqual(self.pack, exchange.handoff(self.store, {'id': self.row['id'], 'revision': 1}))
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(self.pack['base64']))) as z:
            manifest = json.loads(z.read('manifest.json'))
            for name, meta in manifest['files'].items():
                self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(), meta['sha256'])
            handoff = json.loads(z.read('handoff.json'))
            self.assertEqual(handoff['schema'], exchange.SCHEMA)
            self.assertFalse(handoff['native_project'])
            self.assertEqual(handoff['storyboard'][0]['end_us'], 1000000)

    def test_real_full_decode_and_original_unchanged(self):
        original = self.clip.read_bytes()
        ident = self.start(); result = self.finish(ident)
        self.assertEqual(result['state'], 'imported', result)
        receipt = result['receipt']
        self.assertEqual(receipt['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual((self.store.root / receipt['asset_path']).read_bytes(), original)
        self.assertEqual(self.clip.read_bytes(), original)
        self.assertEqual(receipt['full_decode'], 'passed')
        self.assertEqual(receipt['visual_review'], 'pending')
        self.assertEqual(receipt['duration_us'], 1000000)
        self.assertEqual(self.start(), ident)
        video.update(self.store, {'id': self.row['id'], 'expected_revision': 1, 'patch': {'title': 'Changed'}})
        self.assertTrue(exchange.get_import(self.store, ident)['stale'])
        self.assertEqual(exchange.get_import(self.store, ident)['video_revision'], 1)

    def test_invalid_media_fails_not_imported(self):
        bad = Path(self.tmp.name) / 'invalid.mp4'; bad.write_bytes(b'not video')
        ident = exchange.start_import(self.store, {'request_key': 'bad', 'path': str(bad), 'handoff_hash': self.pack['hash']})['id']
        result = self.finish(ident)
        self.assertEqual(result['state'], 'failed')
        self.assertIsNone(result['receipt'])
        self.assertEqual(list((self.store.root / 'renders').iterdir()), [])

    def test_cancel_and_late_worker_cannot_commit(self):
        entered, release = threading.Event(), threading.Event()
        def verifier(path, cancel):
            entered.set(); release.wait(5); return {'full_decode': 'TEST'}
        ident = self.start(verify=verifier)
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(ValueError): self.start('second', verify=verifier)
            self.assertEqual(exchange.cancel_import(self.store, ident)['state'], 'canceled')
        finally:
            release.set(); self.finish(ident)
        self.assertIsNone(exchange.get_import(self.store, ident)['receipt'])
        self.assertEqual(list((self.store.root / 'renders').iterdir()), [])

    def test_expired_job_late_finish_is_fenced(self):
        entered, release = threading.Event(), threading.Event()
        def verifier(path, cancel): entered.set(); release.wait(5); return {'full_decode': 'TEST'}
        ident = self.start(verify=verifier)
        try:
            self.assertTrue(entered.wait(5))
            with self.store.db() as c: c.execute('UPDATE content_video_imports SET deadline=0 WHERE id=?', (ident,))
            self.assertEqual(exchange.get_import(self.store, ident)['state'], 'interrupted')
        finally:
            release.set(); self.finish(ident)
        self.assertIsNone(exchange.get_import(self.store, ident)['receipt'])

    def test_wrong_schema_path_and_request_conflict(self):
        args = {'request_key': 'bad', 'path': str(self.clip), 'handoff_hash': self.pack['hash']}
        for path in ['relative.mp4', str(self.clip.with_suffix('.txt'))]:
            with self.assertRaises(ValueError): exchange.start_import(self.store, {**args, 'path': path})
        link = Path(self.tmp.name) / 'link.mp4'; link.symlink_to(self.clip)
        with self.assertRaises(ValueError): exchange.start_import(self.store, {**args, 'path': str(link)})
        ident = self.start(); self.finish(ident)
        with self.assertRaisesRegex(ValueError, 'request_conflict'):
            exchange.start_import(self.store, {**args, 'request_key': 'import', 'path': str(self.clip.parent / 'other.mp4')})
        with self.store.db() as c: c.execute("UPDATE content_video_handoffs SET snapshot='{}'")
        with self.assertRaisesRegex(ValueError, 'схему'): exchange.start_import(self.store, args)

    def test_no_storyboard_refuses_handoff(self):
        row = video.create(self.store, {'request_key': 'empty', 'data': {'title': 'Empty'}})
        with self.assertRaisesRegex(ValueError, 'сцену'): exchange.handoff(self.store, {'id': row['id'], 'revision': 1})

    def test_preview_auth_head_range_and_changed_asset(self):
        self.addCleanup(video_preview.close_all)
        ident=self.start();result=self.finish(ident)
        url=video_preview.open_preview(self.store,ident)
        base=url.split('/open/')[0]
        with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(base+'/media')
        self.assertEqual(error.exception.code,403)
        client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with client.open(url) as response:self.assertIn('Результат монтажа',response.read().decode())
        with client.open(urllib.request.Request(base+'/media',method='HEAD')) as response:
            self.assertEqual(int(response.headers['Content-Length']),self.clip.stat().st_size)
            self.assertEqual(response.read(),b'')
        with client.open(urllib.request.Request(base+'/media',headers={'Range':'bytes=8-23'})) as response:
            self.assertEqual(response.status,206);self.assertEqual(response.read(),self.clip.read_bytes()[8:24])
        with client.open(urllib.request.Request(base+'/media',headers={'Range':'bytes=-16'})) as response:
            self.assertEqual(response.read(),self.clip.read_bytes()[-16:])
        for suffix in ['bytes=999999999-', 'bytes=0-1,4-5', 'bytes=-0']:
            with self.assertRaises(urllib.error.HTTPError) as error:client.open(urllib.request.Request(base+'/media',headers={'Range':suffix}))
            self.assertEqual(error.exception.code,416)
        with self.assertRaises(urllib.error.HTTPError) as error:client.open(base+'/../content.db')
        self.assertEqual(error.exception.code,404)
        asset=self.store.root/result['receipt']['asset_path'];asset.write_bytes(b'changed')
        with self.assertRaises(urllib.error.HTTPError) as error:client.open(base+'/media')
        self.assertEqual(error.exception.code,409)
        with self.assertRaisesRegex(ValueError,'сумма'):video_preview.open_preview(self.store,ident)


if __name__ == '__main__': unittest.main()
