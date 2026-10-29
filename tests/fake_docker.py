"""Поддельный Docker Engine API на unix-сокете — для проверок панели голосов (lib/ttsctl.php) без Docker.

Умеет то, что зовёт панель: сборку (/build), образы (json, tag), контейнеры (create, start, stop, restart, rename,
delete, json, logs). «Запуск» контейнера службы голосов поднимает настоящую службу server/voices/app.py с поддельной
моделью (TTS_FAKE) на заданном порту и с переменными окружения контейнера — панель видит её /health как настоящую.
fail_start="строка" — контейнер, в переменных которого есть эта строка, «падает» сразу после запуска (для проверки отката); fail_build=True — сборка с ошибкой."""
import json, os, socketserver, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

RUNNER = "import os,sys,threading\nsys.path.insert(0, sys.argv[1])\nimport app\nthreading.Thread(target=app.preload, daemon=True).start()\n" \
         "app.serve(int(sys.argv[2])).serve_forever()\n"


class FakeDocker:
    def __init__(self, sock, voices_dir, port, data_dir):
        self.sock, self.voices_dir, self.port, self.data_dir = sock, voices_dir, port, data_dir
        self.images, self.containers, self.calls, self.ids = {}, {}, [], set()   # ids — все образы, и без имени (как в Docker)
        self.fail_start = self.fail_build = False
        self.builds = 0
        self.runner = os.path.join(os.path.dirname(sock), "fake_runner.py")
        with open(self.runner, "w") as f:
            f.write(RUNNER)
        fd = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def address_string(self):
                return "unix"

            def _out(self, code, obj=None, raw=None, ctype="application/json"):
                b = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                fd.handle(self, "GET")

            def do_POST(self):
                fd.handle(self, "POST")

            def do_DELETE(self):
                fd.handle(self, "DELETE")

        class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

            def handle_error(self, request, client_address):   # клиент ушёл раньше ответа — не шум в выводе проверки
                pass

        if os.path.exists(sock):
            os.remove(sock)
        self.srv = S(sock, H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    # ── контейнер службы ──
    def _spawn(self, c):
        env = dict(os.environ, TTS_FAKE="1", TTS_DATA=self.data_dir)
        for e in c["spec"].get("Env") or []:
            k, _, v = e.partition("=")
            env[k] = v
        env["TTS_DATA"] = self.data_dir
        c["proc"] = subprocess.Popen([sys.executable, self.runner, self.voices_dir, str(self.port)], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _kill(self, c):
        p = c.pop("proc", None)
        if p:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()

    def inspect(self, name):
        c = self.containers[name]
        sp = c["spec"]
        return {"Id": c["id"], "Name": "/" + name, "RestartCount": 0,
                "State": {"Status": c["status"], "StartedAt": "2026-10-14T00:00:00Z", "ExitCode": 1 if c["status"] == "exited" else 0},
                "Config": {"Env": sp.get("Env", []), "Image": sp.get("Image"), "Labels": sp.get("Labels", {})},
                "HostConfig": sp.get("HostConfig", {})}

    def handle(self, h, method):
        u = urlparse(h.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        body = h._body()
        self.calls.append((method, p, q))
        if method == "GET" and p == "/info":
            return h._out(200, {"NCPU": 16, "MemTotal": 64 * 1024 ** 3})
        if method == "POST" and p == "/build":
            self.build_q = q
            self.builds += 1
            ok = not self.fail_build and b"Dockerfile" in body and b"app.py" in body
            lines = [{"stream": "Step 1/6 : FROM python:3.11-slim-bookworm\n"}, {"status": "Downloading torch"}]
            if ok:
                self.images[q.get("t", "x")] = "sha256:" + uuid.uuid4().hex
                self.ids.add(self.images[q.get("t", "x")])
                lines.append({"stream": "Successfully built\n"})
            else:
                lines.append({"error": "pip install failed"})
            return h._out(200, raw=("\n".join(json.dumps(x) for x in lines) + "\n").encode())
        if p.startswith("/images/"):
            ref = p[len("/images/"):].rsplit("/", 1)
            name = ref[0].replace("%2F", "/").replace("%3A", ":")
            if method == "GET" and ref[-1] == "json":
                iid = self.images.get(name) or (name if name in self.ids else None)
                return h._out(200, {"Id": iid, "Created": "2026-10-14T00:00:00Z"}) if iid else h._out(404, {"message": "no such image"})
            if method == "POST" and ref[-1] == "tag":
                iid = self.images.get(name) or (name if name in self.ids else None)
                if not iid:
                    return h._out(404, {"message": "no such image"})
                self.images[q["repo"] + ":" + q["tag"]] = iid
                return h._out(201)
            if method == "DELETE":
                self.images.pop(name, None)
                return h._out(200, [])
        if method == "POST" and p == "/containers/create":
            name = q["name"]
            if name in self.containers:
                return h._out(409, {"message": "Conflict. The container name is already in use"})
            spec = json.loads(body)
            if spec.get("Image") not in self.images:
                return h._out(404, {"message": "No such image"})
            self.containers[name] = {"id": uuid.uuid4().hex, "spec": spec, "status": "created"}
            return h._out(201, {"Id": self.containers[name]["id"]})
        if p.startswith("/containers/"):
            parts = p.split("/")
            name, op = parts[2], (parts[3] if len(parts) > 3 else "")
            c = self.containers.get(name)
            if not c:
                return h._out(404, {"message": "No such container: " + name})
            if method == "GET" and op == "json":
                return h._out(200, self.inspect(name))
            if method == "GET" and op == "logs":
                return h._out(200, raw=b"voices INFO loading model\n", ctype="text/plain")
            if method == "POST" and op in ("start", "restart"):
                if op == "restart":
                    self._kill(c)
                elif c["status"] == "running":
                    return h._out(304)
                if self.fail_start and any(self.fail_start in e for e in c["spec"].get("Env") or []):
                    c["status"] = "exited"
                else:
                    self._spawn(c)
                    c["status"] = "running"
                return h._out(204)
            if method == "POST" and op == "stop":
                self._kill(c)
                c["status"] = "exited"
                return h._out(204)
            if method == "POST" and op == "rename":
                if q["name"] in self.containers:
                    return h._out(409, {"message": "name in use"})
                self.containers[q["name"]] = self.containers.pop(name)
                return h._out(204)
            if method == "DELETE":
                self._kill(c)
                self.containers.pop(name)
                return h._out(204)
        return h._out(404, {"message": "fake docker: " + method + " " + p})

    def close(self):
        for c in list(self.containers.values()):
            self._kill(c)
        self.srv.shutdown()
