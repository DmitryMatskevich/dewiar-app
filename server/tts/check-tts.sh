#!/bin/bash
# Проверка голосового сервера: скажет фразу и сохранит в /tmp/dewiar-tts-test.wav
#   ./check-tts.sh http://127.0.0.1:8091/v1 Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice vivian
URL=${1:-http://127.0.0.1:8091/v1}; MODEL=${2:-Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice}; VOICE=${3:-vivian}
curl -sS -X POST "$URL/audio/speech" -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"voice\":\"$VOICE\",\"language\":\"Russian\",\"input\":\"Привет! Я голос Dewiar, и я работаю на вашем сервере.\",\"response_format\":\"wav\"}" \
  -o /tmp/dewiar-tts-test.wav && file /tmp/dewiar-tts-test.wav
