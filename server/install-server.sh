#!/bin/bash
# Dewiar Server — ставится на ваш сервер (где уже работает vLLM).
#   sudo ./install-server.sh                 — распознавание GigaAM и русские голоса Vosk TTS на CPU (порт 8090)
#   sudo ./install-server.sh --with-tts      — плюс голоса Qwen3-TTS через vLLM-Omni на видеокарте (порт 8091)
#   sudo ./install-server.sh --tts-only      — только голоса Qwen3-TTS (распознавание у вас уже есть своё, например в Docker)
#   Видеокарта для голоса — DEWIAR_TTS_GPU в /etc/dewiar-server.env (номер как в nvidia-smi), доля её памяти — DEWIAR_TTS_GPU_UTIL.
# Настройки: /etc/dewiar-server.env  (ключ, порты, модели). Логи: journalctl -u dewiar-speech -f
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Запустите через sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR=/opt/dewiar-server
WITH_TTS=0; TTS_ONLY=0
case "${1:-}" in --with-tts) WITH_TTS=1 ;; --tts-only) WITH_TTS=1; TTS_ONLY=1 ;; esac

apt-get install -y -q python3-venv ffmpeg >/dev/null
mkdir -p "$DIR"; cp -r "$HERE/speech" "$HERE/tts" "$DIR/"
# числа словами и латиница русскими буквами для голоса — тот же модуль, что у Dewiar на компьютере
for f in "$HERE/../core/speechnorm.py" "$HERE/core/speechnorm.py"; do [ -f "$f" ] && cp "$f" "$DIR/speech/"; done
id dewiar >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin dewiar

if [ ! -f /etc/dewiar-server.env ]; then
  cat > /etc/dewiar-server.env <<ENV
# Ключ, который клиенты Dewiar указывают в настройках (пусто = без ключа, только для доверенной сети)
DEWIAR_SPEECH_KEY=$(head -c 18 /dev/urandom | base64 | tr -d '/+=')
DEWIAR_SPEECH_PORT=8090
DEWIAR_SPEECH_MODEL=gigaam-v3-e2e-rnnt
# Голоса на CPU в том же сервисе (/v1/audio/speech), ~0,2 ГБ, скачиваются при первом запуске; 0 — выключить
DEWIAR_SPEECH_TTS=vosk-model-tts-ru-0.9-multi
# Голос Qwen3-TTS (--with-tts): 0.6B — около 4 ГБ видеопамяти, 1.7B — заметно лучше, около 8 ГБ
DEWIAR_TTS_PORT=8091
DEWIAR_TTS_MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
# видеокарта для голоса — номер как в nvidia-smi; доля её памяти (0.35 от 24 ГБ — около 8,4 ГБ)
DEWIAR_TTS_GPU=0
DEWIAR_TTS_GPU_UTIL=0.35
HF_HOME=$DIR/hf
ENV
  chmod 640 /etc/dewiar-server.env; chgrp dewiar /etc/dewiar-server.env
fi

grep -q '^DEWIAR_TTS_GPU=' /etc/dewiar-server.env || echo 'DEWIAR_TTS_GPU=0' >> /etc/dewiar-server.env
if [ $TTS_ONLY = 0 ]; then
echo "== Распознавание (GigaAM)"
python3 -m venv "$DIR/speech-venv"
"$DIR/speech-venv/bin/pip" install -q --upgrade pip
ORT=onnxruntime; command -v nvidia-smi >/dev/null && ORT=onnxruntime-gpu
"$DIR/speech-venv/bin/pip" install -q fastapi "uvicorn[standard]" python-multipart numpy "onnx-asr[hub]" "$ORT"
# голоса Vosk TTS: без зависимостей — иначе pip поставит onnxruntime для CPU поверх onnxruntime-gpu
"$DIR/speech-venv/bin/pip" install -q --no-deps vosk-tts && "$DIR/speech-venv/bin/pip" install -q requests tokenizers tqdm
grep -q '^DEWIAR_SPEECH_TTS=' /etc/dewiar-server.env || echo 'DEWIAR_SPEECH_TTS=vosk-model-tts-ru-0.9-multi' >> /etc/dewiar-server.env
cat > /etc/systemd/system/dewiar-speech.service <<UNIT
[Unit]
Description=Dewiar Speech — распознавание речи GigaAM и голоса Vosk TTS (OpenAI API)
After=network-online.target
[Service]
User=dewiar
EnvironmentFile=/etc/dewiar-server.env
WorkingDirectory=$DIR/speech
ExecStart=/bin/sh -c 'exec $DIR/speech-venv/bin/uvicorn server:app --host 0.0.0.0 --port \${DEWIAR_SPEECH_PORT}'
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
fi
mkdir -p "$DIR/hf"; chown -R dewiar:dewiar "$DIR"

if [ $WITH_TTS = 1 ]; then
  echo "== Голос (Qwen3-TTS в vLLM-Omni) — это самый тяжёлый шаг, 5–15 минут"
  python3 -m venv "$DIR/tts-venv"
  "$DIR/tts-venv/bin/pip" install -q --upgrade pip
  "$DIR/tts-venv/bin/pip" install -q "git+https://github.com/vllm-project/vllm-omni.git"
  cat > /etc/systemd/system/dewiar-tts.service <<UNIT
[Unit]
Description=Dewiar TTS — Qwen3-TTS через vLLM-Omni (OpenAI /v1/audio/speech)
After=network-online.target
[Service]
User=dewiar
EnvironmentFile=/etc/dewiar-server.env
Environment=CUDA_DEVICE_ORDER=PCI_BUS_ID
ExecStart=/bin/sh -c 'exec env CUDA_VISIBLE_DEVICES=\${DEWIAR_TTS_GPU:-0} $DIR/tts-venv/bin/vllm serve \${DEWIAR_TTS_MODEL} --omni --host 0.0.0.0 --port \${DEWIAR_TTS_PORT} --gpu-memory-utilization \${DEWIAR_TTS_GPU_UTIL}'
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
UNIT
  chown -R dewiar:dewiar "$DIR"
fi

# Описание сервера для клиентов (/.well-known/dewiar.json): адреса моделей, код доступа — заглушкой ${KEY}
set -a; . /etc/dewiar-server.env; set +a     # экспорт — генератору описания нужны эти переменные
IP=$(hostname -I | awk '{print $1}')
HOST="${DEWIAR_PUBLIC_HOST:-$IP}"
VLLM_URL="${DEWIAR_VLLM_URL:-http://$HOST:8000/v1}"
mkdir -p /etc/dewiar-server
python3 - "$HOST" "$VLLM_URL" "$WITH_TTS" > /etc/dewiar-server/dewiar.json <<'PY'
import json, os, socket, sys
host, vllm, tts = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
key = bool(os.environ.get("DEWIAR_SPEECH_KEY"))
m = {"dewiar": 1, "name": f"Dewiar Server ({socket.gethostname()})",
     "llm": {"active": "dewiar-server", "profiles": {"dewiar-server": {
         "name": "Dewiar Server — vLLM", "kind": "openai", "base_url": vllm,
         "api_key": "${KEY}" if os.environ.get("DEWIAR_VLLM_USES_KEY") == "1" else "", "model": "", "disable_thinking": True}}},
     "stt": {"engine": "remote", "remote": {"base_url": f"http://{host}:{os.environ.get('DEWIAR_SPEECH_PORT', '8090')}/v1",
                                            "api_key": "${KEY}" if key else "", "model": "gigaam-v3", "language": "ru"}}}
if key:
    m["key"] = {"required": True, "hint": "Код доступа выдаёт владелец сервера"}
if os.environ.get("DEWIAR_SPEECH_TTS", "1") not in ("", "0", "off"):     # голоса Vosk TTS в этом же сервисе
    m["tts"] = {"engine": "remote", "remote": {"base_url": f"http://{host}:{os.environ.get('DEWIAR_SPEECH_PORT', '8090')}/v1",
                "api_key": "${KEY}" if key else "", "model": "vosk-tts", "sample_rate": 22050}}
if tts:
    m["tts"] = {"engine": "remote", "remote": {"base_url": f"http://{host}:{os.environ.get('DEWIAR_TTS_PORT', '8091')}/v1",
                "model": os.environ.get("DEWIAR_TTS_MODEL", ""), "sample_rate": 24000, "extra": {"language": "Russian"}}}
print(json.dumps(m, ensure_ascii=False, indent=2))
PY
chmod 644 /etc/dewiar-server/dewiar.json

systemctl daemon-reload
[ $TTS_ONLY = 0 ] && systemctl enable --now dewiar-speech.service
[ $WITH_TTS = 1 ] && systemctl enable --now dewiar-tts.service
if [ $TTS_ONLY = 1 ]; then
  echo
  echo "Готово: голос Qwen3-TTS ставится на видеокарту $DEWIAR_TTS_GPU (первый запуск скачивает модель, 5–15 минут):"
  echo "  journalctl -u dewiar-tts -f        — ждите строку о запуске сервера на порту $DEWIAR_TTS_PORT"
  echo "Потом одна строка в .env сайта — и голос появится у всех (телефоны, компьютеры, у каждого персонажа свой):"
  echo "  DEWIAR_CLOUD_TTS=http://$IP:$DEWIAR_TTS_PORT/v1"
  exit 0
fi
echo
echo "Готово. На компьютерах: студия Dewiar → «Подключение» → адрес  $HOST:$DEWIAR_SPEECH_PORT"
echo "  (код доступа: $DEWIAR_SPEECH_KEY). Описание сервера: /etc/dewiar-server/dewiar.json — его можно править."
echo "  Своё доменное имя: DEWIAR_PUBLIC_HOST=dewiar.ru в /etc/dewiar-server.env и повторите установку."
echo "  Ссылка для сайта: dewiar://connect?server=$HOST:$DEWIAR_SPEECH_PORT"
echo
echo "Или вручную:"
echo "  Слух  → «Сервер распознавания»: адрес http://$IP:$DEWIAR_SPEECH_PORT/v1  ключ $DEWIAR_SPEECH_KEY"
echo "  Голос → «Сервер синтеза»: адрес http://$IP:$DEWIAR_SPEECH_PORT/v1  ключ $DEWIAR_SPEECH_KEY  (голоса Vosk TTS)"
echo "  Сайт dewiar.pro (облако): в .env  DEWIAR_CLOUD_TTS=http://$IP:$DEWIAR_SPEECH_PORT/v1  DEWIAR_CLOUD_TTS_KEY=$DEWIAR_SPEECH_KEY"
echo "    — и телефоны без русского голоса заговорят голосом Dewiar"
[ $WITH_TTS = 1 ] && echo "  Голос Qwen3 → «Сервер синтеза»: адрес http://$IP:$DEWIAR_TTS_PORT/v1  модель $DEWIAR_TTS_MODEL  частота 24000"
echo "Первый запуск скачивает модели — следите: journalctl -u dewiar-speech -u dewiar-tts -f"
