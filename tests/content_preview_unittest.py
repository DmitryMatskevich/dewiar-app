"""Loopback-only HTTP fixture; never opens a browser or production server."""
import hashlib
import http.client
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.sites import preview

class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.addCleanup(preview.close_all);self.root=Path(self.tmp.name)
        self.dist=self.root/'dist';self.public=self.root/'public'
        (self.dist/'articles/test').mkdir(parents=True);self.public.mkdir()
        self.article=self.dist/'articles/test/index.html';self.article.write_text('<html><body><p>Статья</p><form action="/save.php"></form></body></html>')
        self.receipt={'dist':str(self.dist),'public_root':str(self.public),'article_path':'/articles/test/','article_sha256':hashlib.sha256(self.article.read_bytes()).hexdigest()}
        self.url=urlsplit(preview.open_preview(self.receipt));self.cookie=None
    def request(self,path,method='GET'):
        c=http.client.HTTPConnection(self.url.hostname,self.url.port,timeout=3)
        try:
            c.request(method,path,headers={'Cookie':self.cookie} if self.cookie else {})
            r=c.getresponse();return r.status,dict(r.getheaders()),r.read()
        finally:c.close()
    def login(self):
        status,headers,_=self.request(self.url.path);self.assertEqual(status,303);self.cookie=headers['Set-Cookie'].split(';')[0]
    def test_auth_inert_html_head_and_media(self):
        self.assertEqual(self.request('/articles/test/')[0],403);self.login()
        status,headers,body=self.request('/articles/test/');self.assertEqual(status,200)
        self.assertIn('ПРЕДПРОСМОТР',body.decode());self.assertIn("form-action 'none'",headers['Content-Security-Policy'])
        self.assertEqual(self.request('/articles/test/','HEAD')[2],b'')
        (self.public/'image.png').write_bytes(b'image');self.assertEqual(self.request('/image.png')[2],b'image')
        self.assertEqual(self.request('/save.php','POST')[0],501)
    def test_traversal_symlink_and_server_source_denied(self):
        self.login();secret=self.root/'private.html';secret.write_text('secret')
        (self.public/'save.php').write_text('<?php secret ?>');(self.public/'.env').write_text('secret')
        (self.public/'escape.html').symlink_to(secret);(self.public/'nested').mkdir();(self.public/'nested/index.html').symlink_to(secret)
        for path in ['/../private.html','/%2e%2e/private.html','/escape.html','/nested/','/save.php','/.env','/%00']:
            self.assertEqual(self.request(path)[0],404,path)
    def test_missing_changed_artifact_and_independent_cookie(self):
        self.login();cookie1=self.cookie
        other=self.root/'other';(other/'articles/test').mkdir(parents=True);(other/'articles/test/index.html').write_bytes(self.article.read_bytes())
        self.url=urlsplit(preview.open_preview({**self.receipt,'dist':str(other)}));self.assertEqual(self.request('/articles/test/')[0],403)
        self.login();self.assertNotEqual(self.cookie.split('=')[0],cookie1.split('=')[0])
        self.article.write_text('changed')
        with self.assertRaises(ValueError):preview.open_preview(self.receipt)

if __name__=='__main__':unittest.main()
