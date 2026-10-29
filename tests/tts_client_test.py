"""Клиент удалённого голоса против трёх форм ответа: сырой PCM (vLLM-Omni), WAV, SSE с base64."""
import base64, io, json, sys, threading, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, "core")
from tts import RemoteTTS
PCM = (b"\x01\x02" * 24000)          # 1 с тишины-шаблона при 24 кГц
MODE = {"m": "raw"}; SEEN = {}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        SEEN.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        if MODE["m"] == "sse":
            self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for i in range(0, len(PCM), 4800):
                self.wfile.write(f"data: {json.dumps({'audio': base64.b64encode(PCM[i:i+4800]).decode()})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        elif MODE["m"] == "wav":
            b = io.BytesIO(); w = wave.open(b, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(PCM); w.close()
            self.send_header("Content-Type", "audio/wav"); self.end_headers(); self.wfile.write(b.getvalue())
        else:
            self.send_header("Content-Type", "application/octet-stream"); self.end_headers()
            for i in range(0, len(PCM), 3001): self.wfile.write(PCM[i:i+3001]); self.wfile.flush()
srv = ThreadingHTTPServer(("127.0.0.1", 8131), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
t = RemoteTTS({"base_url": "http://127.0.0.1:8131/v1", "model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice", "sample_rate": 24000, "extra": {"language": "Russian"}})
for m, rate in [("raw", 24000), ("wav", 22050), ("sse", 24000)]:
    MODE["m"] = m
    chunks = list(t.synth("Привет", {"remote": "vivian"}, 1.0))
    data = b"".join(c for _, c in chunks)
    assert data == PCM, (m, len(data)); assert all(r == rate for r, _ in chunks), m
    print(f"✅ {m}: {len(chunks)} кусков, {rate} Гц, звук совпал")
assert SEEN["voice"] == "vivian" and SEEN["language"] == "Russian" and SEEN["stream"] is True
print("✅ запрос: голос, язык и стриминг переданы")
