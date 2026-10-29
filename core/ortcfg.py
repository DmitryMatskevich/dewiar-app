"""Число потоков ONNX Runtime — по желанию (DEWIAR_ORT_THREADS): голоса (vosk-tts, Piper) создают сессии с настройками
по умолчанию — поток на каждое ядро. В 0.33.0 их ограничивали половиной ядер, чтобы не грузить процессор на старте, —
но голос на 4 ядрах стал синтезироваться медленнее, чем звучит (паузы между фразами), поэтому по умолчанию теперь
ничего не меняем. limit() подменяет onnxruntime.InferenceSession, только если задано DEWIAR_ORT_THREADS: сессиям без
своего числа потоков — это число и ожидание без холостого кручения. Вызов — в core/main.py, до создания сессий."""
import os
import threading

# Тяжёлые загрузки (GigaAM — слух, Vosk TTS — голоса: сотни мегабайт моделей и словарь ударений) — по одной за раз.
# Вместе на пике они забирали память и все ядра сразу (отзыв 11.10: включил Vosk при GigaAM — компьютер завис).
HEAVY = threading.Lock()


def threads():
    """Сколько потоков задал человек (DEWIAR_ORT_THREADS); 0 — не задано, решает ONNX Runtime (по ядру на поток)."""
    try:
        return max(0, int(os.environ.get("DEWIAR_ORT_THREADS") or 0))
    except ValueError:
        return 0


def limit():
    try:
        import onnxruntime as ort
    except Exception:  # noqa: BLE001 — нет onnxruntime (тесты, урезанная сборка) — и не надо
        return False
    base = ort.InferenceSession
    if getattr(base, "_dewiar", False):
        return True
    n = threads()
    if not n:
        return False

    class Session(base):
        _dewiar = True

        def __init__(self, path_or_bytes, sess_options=None, *args, **kwargs):
            if sess_options is None:
                sess_options = ort.SessionOptions()
            try:
                if not sess_options.intra_op_num_threads:          # 0 — «сколько ядер» — ограничиваем
                    sess_options.intra_op_num_threads = n
                if not sess_options.inter_op_num_threads:
                    sess_options.inter_op_num_threads = 1
                sess_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
                sess_options.add_session_config_entry("session.inter_op.allow_spinning", "0")
            except Exception:  # noqa: BLE001 — старая версия без этих настроек: как есть
                pass
            super().__init__(path_or_bytes, sess_options, *args, **kwargs)

    ort.InferenceSession = Session
    return True
