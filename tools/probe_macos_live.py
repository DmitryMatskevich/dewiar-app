"""Optional real local Ollama acceptance, separate from deterministic regression checks.

Isolated core and synthetic images; only memory, notes, arithmetic and reminders enabled.
The loopback gateway bounds context memory without changing the user's Ollama settings.
It forwards actual model output unchanged. No external services or model downloads.
"""
import asyncio
import argparse
import json
import sys
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from exam import Core, make_image
from tools import TOOLS

MODEL = "gemma4:12b"
UPSTREAM = "http://127.0.0.1:11434"
HITS = []
THINK = "off"
MAX_TOKENS = 768
OUTPUT = ROOT / "work/video-checks/live-local-model.json"


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/api/chat" and body["model"] == MODEL
        body.setdefault("options", {})["num_ctx"] = 8192
        body["keep_alive"] = "60s"
        hit = {"images": any(m.get("images") for m in body.get("messages", [])), "tools": len(body.get("tools", [])), "thinking_chars": 0}
        HITS.append(hit)
        try:
            with httpx.stream("POST", UPSTREAM + self.path, json=body, timeout=120) as response:
                self.send_response(response.status_code)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/x-ndjson"))
                self.send_header("Connection", "close")
                self.end_headers()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        hit["thinking_chars"] += len(json.loads(line).get("message", {}).get("thinking") or "")
                    except ValueError:
                        pass
                    self.wfile.write((line + "\n").encode())
                    self.wfile.flush()
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            pass


def compact_numbers(answer):
    return answer.replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")


async def main():
    loaded = httpx.get(UPSTREAM + "/api/ps", timeout=5).json().get("models", [])
    installed = httpx.get(UPSTREAM + "/api/tags", timeout=5).json().get("models", [])
    selected = next((m for m in installed if m["name"] == MODEL), None)
    if selected is None:
        raise SystemExit("Local model is not installed; no download attempted")
    if ":cloud" in MODEL or selected.get("remote_model") or selected.get("remote_host"):
        raise SystemExit("This probe only accepts local weights, not Ollama cloud models")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = []
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="dewiar-live-video-") as folder:
            profile = {"kind": "ollama", "base_url": f"http://127.0.0.1:{server.server_port}", "model": MODEL,
                       "max_tokens": MAX_TOKENS, "think": THINK, "temperature": 0.6 if THINK == "on" else 0, "tools_mode": "native"}
            core = Core(profile, folder, lean=True)
            try:
                core.start()
                await core.connect()
                enabled = {name: name in {"calc", "remember", "save_note", "set_reminder", "list_reminders"} for name in TOOLS}
                await core.call("settings_save", patch={"tools": {"enabled": enabled}, "privacy": {"local_only": True}})

                async def say(text, image=None):
                    answer, events = await core.say(text, image=image, timeout=180)
                    if not answer:
                        await core.call("say", text="Стоп")
                    return answer, events

                await say("Запомни: тестовый проект называется Янтарь-481.")
                await say("Начнём новый разговор")
                before = len(HITS)
                answer, _ = await say("Как называется тестовый проект, который я просил запомнить?")
                results.append({"id": "memory", "passed": "481" in answer and "янтар" in answer.lower() and len(HITS) > before, "answer": answer})
                print("LIVE memory:", results[-1]["passed"], flush=True)

                await say("Начнём новый разговор")
                answer, _ = await say("Сколько всего заплачено по чеку на фото и какой товар самый дорогой?", make_image("receipt"))
                numbers = compact_numbers(answer)
                results.append({"id": "receipt", "passed": "1437.5" in numbers and "520" in numbers and "сыр" in answer.lower(), "answer": answer})
                print("LIVE receipt:", results[-1]["passed"], flush=True)

                await say("Начнём новый разговор")
                answer, _ = await say("Прочитай стикер на фото и поставь напоминание на указанное время.", make_image("sticker"))
                reminders = await core.call("reminders")
                ok = any("ольг" in r.get("text", "").lower() and datetime.fromisoformat(r["at"]).hour == 17 and datetime.fromisoformat(r["at"]).minute == 0 for r in reminders)
                results.append({"id": "sticker", "passed": ok, "answer": answer, "reminders": reminders})
                print("LIVE sticker:", results[-1]["passed"], flush=True)
            finally:
                await core.stop()
    finally:
        server.shutdown()
        server.server_close()
        # Leave models that were loaded before this probe alone.
        if not any(m["name"] == MODEL for m in loaded):
            try:
                httpx.post(UPSTREAM + "/api/generate", json={"model": MODEL, "keep_alive": 0}, timeout=15)
            except httpx.HTTPError:
                pass
        destination = OUTPUT
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({"generated_at": datetime.now().astimezone().isoformat(), "model": MODEL, "think": THINK, "max_tokens": MAX_TOKENS, "gateway": "real Ollama, context limited to 8192", "seconds": round(time.monotonic() - started),
                                            "requests": HITS, "checks": results, "full_live_acceptance": False}, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(0 if len(results) == 3 and all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--thinking", choices=("on", "off"), default=THINK)
    parser.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    MODEL, THINK, MAX_TOKENS, OUTPUT = args.model, args.thinking, args.max_tokens, args.output.resolve()
    asyncio.run(main())
