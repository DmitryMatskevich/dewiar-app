# Голоса Dewiar — Qwen3-TTS и ESpeech на своей видеокарте

Служба живых голосов для персонажей Dewiar: встроенные дикторы Qwen3-TTS (ускорены faster-qwen3-tts), свой голос по
образцу записи (5–12 секунд и её текст) — его озвучивает ESpeech, обученный на русской речи, с ударениями RUAccent
(своё ударение — «+» перед гласной), и голос по описанию словами (Qwen VoiceDesign → проба становится образцом). API — как у OpenAI (`POST /v1/audio/speech`), поэтому Dewiar на компьютере и
телефоне подключается к ней как к любому серверу голоса, а персонаж говорит своим голосом: приложения передают поле
`persona`, служба смотрит в `voices.json`, кому какой голос назначен.

```bash
docker build -t dewiar/tts server/voices
docker run -d --gpus '"device=0"' -p 8000:8000 -v tts_models:/models -v tts_voices:/data --name dewiar-tts dewiar/tts
curl -s localhost:8000/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"input":"Привет! Я на связи.","voice":"serena","response_format":"wav"}' -o hello.wav
```

Свой голос: `curl -F file=@anna.wav -F title="Анна" -F gender=female -F text="что сказано в записи" localhost:8000/v1/voices`,
назначить персонажу: `curl -d '{"personas":{"lira":"anna"}}' -H 'Content-Type: application/json' localhost:8000/v1/voices/config`.

Видеопамять: около 2–3 ГБ на модель (0.6B), держится не больше `TTS_MAX_MODELS` моделей; большая модель голосов по
описанию (1.7B) загружается только на время пробы. Все настройки окружения — в начале `app.py`.
Модели Qwen3-TTS — Apache-2.0.
