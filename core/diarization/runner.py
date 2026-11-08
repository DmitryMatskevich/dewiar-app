"""Запуск отдельного процесса разбиения по участникам из ядра: прогресс, таймаут, отмена, журнал.

Собранное приложение — тот же исполняемый файл с флагом --diarize-worker (tools/macos_entry.py), из исходников —
текущий Python со скриптом worker.py. Ядро sherpa-onnx не импортирует.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .schema import turns_from_dicts


def worker_cmd():
    override = os.environ.get("DEWIAR_DIARIZE_WORKER")          # тесты и отладка: свой интерпретатор
    if override:
        return override.split("\x1f") if "\x1f" in override else [override, "-B", str(Path(__file__).with_name("worker.py"))]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--diarize-worker"]
    return [sys.executable, "-B", str(Path(__file__).with_name("worker.py"))]


class DiarizationJob:
    """Один запуск. Создаётся сразу после записи: идёт параллельно с распознаванием."""

    def __init__(self, task, log_path=None, on_progress=None, cmd=None):
        self.task = dict(task)
        self.result = None
        self.error = ""
        self.progress = 0.0
        self._on_progress = on_progress
        self._log = None
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS=str(task.get("num_threads") or 2))
        if not getattr(sys, "frozen", False):
            env.pop("DYLD_LIBRARY_PATH", None)
        try:
            if log_path:
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                self._log = open(log_path, "a", encoding="utf-8")
            self.proc = subprocess.Popen(cmd or worker_cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=self._log or subprocess.DEVNULL, text=True, encoding="utf-8",
                                         env=env, start_new_session=True)
            self.proc.stdin.write(json.dumps(self.task, ensure_ascii=False))
            self.proc.stdin.close()
        except Exception as e:  # noqa: BLE001 — процесс не стартовал: причина — в карточке встречи
            self.proc = None
            self.error = f"процесс разбиения не запустился: {e}"[:300]
            self._close_log()
            self._t = None
            return
        self._t = threading.Thread(target=self._read, name="diarize-read", daemon=True)
        self._t.start()

    def _close_log(self):
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue                              # посторонний вывод библиотек
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "progress":
                    self.progress = float(msg.get("value") or 0.0)
                    if self._on_progress:
                        try:
                            self._on_progress(self.progress)
                        except Exception:  # noqa: BLE001 — окно закрыто: обработка продолжается
                            pass
                elif kind == "result":
                    self.result = msg
                elif kind == "error":
                    self.error = str(msg.get("message") or "ошибка разбиения")[:500]
        except (OSError, ValueError):
            pass

    def wait(self, timeout=None):
        """-> сообщение result или None (причина — в .error)."""
        if self.proc is None:
            return None
        if self._t is not None:
            self._t.join(timeout)
            if self._t.is_alive():
                self.cancel()
                self.error = "превышено время ожидания разбиения по участникам"
                self._t.join(5)
        try:
            code = self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.cancel()
            code = -1
        self._close_log()
        if self.result is None and not self.error:
            self.error = f"процесс разбиения завершился с кодом {code}"
        return self.result

    def turns(self, timeout=None):
        r = self.wait(timeout)
        return turns_from_dicts(r.get("turns")) if r else None

    def cancel(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass


def start(wav, segmentation, embedding, num_speakers=0, max_speakers=0, threshold=0.4, num_threads=2,
          log_path=None, on_progress=None, cmd=None):
    task = {"wav": str(wav), "segmentation": str(segmentation), "embedding": str(embedding),
            "num_speakers": int(num_speakers or 0), "max_speakers": int(max_speakers or 0),
            "threshold": float(threshold or 0.4), "num_threads": int(num_threads or 2), "started": time.time()}
    return DiarizationJob(task, log_path=log_path, on_progress=on_progress, cmd=cmd)
