"""tools/yandex_music_auth_check.py на поддельном сервере Яндекса: вход по коду (ожидание, «реже», отказ),
статус аккаунта, подпись запроса текста, LRC, продление, вставка токена. Токен не должен попасть в вывод."""
import base64
import hashlib
import hmac
import io
import json
import os
import sys
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import yandex_music_auth_check as chk  # noqa: E402

for _var in ("no_proxy", "NO_PROXY"):           # поддельный сервер на 127.0.0.1 — мимо системного прокси
    os.environ[_var] = ",".join(filter(None, [os.environ.get(_var), "127.0.0.1", "localhost"]))

TOKEN = "y0_TESTaccessTOKEN0123456789abcdef"
NEW_TOKEN = "y0_NEWaccessTOKEN9876543210fedcba"
REFRESH = "1:REFRESHtokenSECRETvalue000111"
LRC = "[ti:Песня]\n[00:01.20]Первая строка\n[00:05.50]Вторая строка\n[00:09.00]Третья строка\n"


class Fake:
    """Сценарий сервера: что отвечать на опрос токена и есть ли текст у трека."""

    def __init__(self, polls=("authorization_pending", "slow_down", "ok"), lyrics=True, refresh_token=True):
        self.polls = list(polls)
        self.lyrics = lyrics
        self.refresh_token = refresh_token
        self.seen = []
        self.base = ""


def handler(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            raw = (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            form = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
            form = {k: v[0] for k, v in form.items()}
            fake.seen.append(("POST", self.path, form))
            if self.path == "/device/code":
                assert form["client_id"] == chk.CLIENT_ID and form["device_id"].startswith("dewiar-check-")
                return self.send(200, {"device_code": "DC", "user_code": "ABCD1234", "interval": 1, "expires_in": 300,
                                       "verification_url": fake.base + "/device"})
            if self.path == "/token" and form.get("grant_type") == "device_code":
                assert form["code"] == "DC" and form["client_secret"] == chk.CLIENT_SECRET
                step = fake.polls.pop(0)
                if step == "ok":
                    body = {"access_token": TOKEN, "token_type": "bearer", "expires_in": 31536000}
                    if fake.refresh_token:
                        body["refresh_token"] = REFRESH
                    return self.send(200, body)
                return self.send(400, {"error": step, "error_description": step})
            if self.path == "/token" and form.get("grant_type") == "refresh_token":
                assert form["refresh_token"] == REFRESH
                return self.send(200, {"access_token": NEW_TOKEN, "refresh_token": REFRESH + "2", "expires_in": 31536000})
            self.send(404, {"error": "not found"})

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            fake.seen.append(("GET", u.path, q))
            if u.path == "/lrc":
                return self.send(200, LRC, "text/plain; charset=utf-8")
            if u.path == "/txt":
                return self.send(200, "Первая строка\nВторая строка\n", "text/plain; charset=utf-8")
            auth = self.headers.get("Authorization")
            if auth not in (f"OAuth {TOKEN}", f"OAuth {NEW_TOKEN}"):
                return self.send(401, {"error": {"name": "session-expired"}})
            assert self.headers.get("X-Yandex-Music-Client") == chk.MUSIC_CLIENT
            if u.path == "/account/status":
                return self.send(200, {"result": {"account": {"login": "tester", "uid": 7}, "plus": {"hasPlus": True}}})
            if u.path == "/search":
                assert q["type"] == "track" and q["text"]
                return self.send(200, {"result": {"tracks": {"results": [
                    {"id": "123:456", "title": "Кукушка", "artists": [{"name": "Кино"}]}]}}})
            if u.path == "/tracks/123/lyrics":
                want = base64.b64encode(hmac.new(chk.SIGN_KEY, f"123{q['timeStamp']}".encode(), hashlib.sha256).digest()).decode()
                assert q["sign"] == want, "подпись запроса текста не совпала"
                if not fake.lyrics and q["format"] == "LRC":
                    return self.send(404, {"error": {"name": "not-found"}})
                return self.send(200, {"result": {"downloadUrl": fake.base + ("/lrc" if q["format"] == "LRC" else "/txt")}})
            self.send(404, {"error": "not found"})

    return H


class CheckTest(unittest.TestCase):
    def serve(self, fake):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler(fake))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        fake.base = f"http://127.0.0.1:{srv.server_address[1]}"
        return fake.base

    def run_check(self, fake, *extra):
        base = self.serve(fake)
        out = io.StringIO()
        sleeps = []
        with patch.object(chk, "SLEEP", sleeps.append):
            code = chk.main(["--no-browser", "--oauth-url", base, "--api-url", base, *extra], out=out)
        return code, out.getvalue(), sleeps

    def assert_no_secrets(self, text):
        for s in (TOKEN, NEW_TOKEN, REFRESH):
            self.assertNotIn(s, text)

    def test_device_flow_full(self):
        fake = Fake()
        code, out, sleeps = self.run_check(fake, "--refresh")
        self.assertEqual(code, 0, out)
        self.assertEqual(sleeps, [1, 1, 6], "после slow_down интервал должен вырасти на 5 с")
        self.assertIn("ABCD1234", out)
        self.assertIn("вход: tester; Плюс: да", out)
        self.assertIn("Кино — Кукушка (id 123)", out)
        self.assertIn("строк с метками времени: 3", out)
        self.assertIn("[00:01.20]Первая строка", out)
        self.assertIn("новый вид y0_", out)
        self.assertIn("срок 365 дн.", out)
        self.assertIn("✔ Продление токена", out)
        self.assert_no_secrets(out)

    def test_access_denied(self):
        code, out, _ = self.run_check(Fake(polls=("authorization_pending", "access_denied")))
        self.assertEqual(code, 1)
        self.assertIn("вход отклонён", out)

    def test_expired_code(self):
        code, out, _ = self.run_check(Fake(polls=("expired_token",)))
        self.assertEqual(code, 1)
        self.assertIn("код устарел", out)

    def test_no_refresh_token(self):
        code, out, _ = self.run_check(Fake(polls=("ok",), refresh_token=False), "--refresh")
        self.assertEqual(code, 0, out)
        self.assertIn("не выдан — раз в год понадобится повторный вход", out)
        self.assertIn("– Продление токена", out)

    def test_track_without_lrc(self):
        code, out, _ = self.run_check(Fake(polls=("ok",), lyrics=False), "--track",
                                      "https://music.yandex.ru/album/456/track/123")
        self.assertEqual(code, 0, out)
        self.assertIn("нет текста с метками времени (обычный текст есть)", out)

    def test_paste_url_string(self):
        pasted = f"access_token={TOKEN}&token_type=bearer&expires_in=31536000&cid=abc"
        with patch.object(chk.getpass, "getpass", return_value=pasted):
            code, out, _ = self.run_check(Fake(), "--paste")
        self.assertEqual(code, 0, out)
        self.assertIn("Токен получен из вставки", out)
        self.assertIn("срок 365 дн.", out)
        self.assert_no_secrets(out)

    def test_paste_bad_token(self):
        with patch.object(chk.getpass, "getpass", return_value="y0_WRONGtoken000000000000000"):
            code, out, _ = self.run_check(Fake(), "--paste")
        self.assertEqual(code, 1)
        self.assertIn("токен не принят (401)", out)

    def test_parse_helpers(self):
        self.assertEqual(chk.parse_pasted(f"https://music.yandex.ru/#access_token={TOKEN}&token_type=bearer")[0], TOKEN)
        self.assertEqual(chk.parse_pasted(f"  {TOKEN}\n")[0], TOKEN)
        self.assertEqual(chk.track_id_from("https://music.yandex.ru/album/1/track/42?utm=x"), "42")
        self.assertEqual(chk.track_id_from("42:1"), "42")
        with self.assertRaises(chk.Fail):
            chk.track_id_from("abc")

    def test_no_network(self):
        out = io.StringIO()
        code = chk.main(["--no-browser", "--oauth-url", "http://127.0.0.1:9", "--timeout", "2"], out=out)
        self.assertEqual(code, 1)
        self.assertIn("нет связи с 127.0.0.1:9", out.getvalue())


if __name__ == "__main__":
    unittest.main()
