# Распознавание речи на сервере (GigaAM на видеокарте)

Приложения Dewiar отправляют фразу на сервер и получают текст за доли секунды — даже на слабом компьютере.
API как у OpenAI (`/v1/audio/transcriptions`), вход — `api_token` пользователя сайта (тот же, что для Знаний).

## Установка (5 минут)

1. Скопировать папку `server/stt` на сервер: `/var/www/dwr/src/dewiar-stt/`.
2. `docker-compose.stt.yml` — в `/var/www/dwr/`, в `.env` дописать его в `COMPOSE_FILE` (через двоеточие).
3. Содержимое `nginx-stt.conf` вставить в `nginx.conf` рядом с блоком `/mnote/`.
4. `docker compose up -d --build dewiar-stt` и перезапустить app (nginx).
5. В `dwrapp/login.php` вписать адрес: `const DWR_STT_URL = '/stt/v1';` — приложения узнают о сервисе сами
   после входа в аккаунт (студия → «Слух» → «Распознавание на сервере Dewiar»).

Проверка:

    curl https://dewiar.pro/stt/health
    curl -H "Authorization: Bearer <api_token>" -F file=@фраза.wav https://dewiar.pro/stt/v1/audio/transcriptions

Без видеокарты сервис тоже работает (на процессоре, медленнее). Модель скачивается при первом запуске в
`storage/dewiar-stt` (~250 МБ). Журнал: `docker logs -f dewiar-stt` — там видно, сколько миллисекунд заняла фраза.

## Голос с сервера (по желанию)

Если на сервере работает синтез речи (например, Qwen3-TTS через vLLM-Omni из `server/tts`), этот же сервис
пускает к нему пользователей сайта по их токену: в `docker-compose.stt.yml` раскомментируйте `TTS_UPSTREAM`
(адрес TTS, например `http://host.docker.internal:8091/v1`) и, если нужно, `TTS_UPSTREAM_KEY`. Затем в `login.php`:
`const DWR_TTS_URL = '/stt/v1';` — в приложении появится «Голос» → «Голос с сервера Dewiar».

Проверка: `curl -H "Authorization: Bearer <api_token>" -d '{"input":"Привет","voice":"Ryan"}' https://dewiar.pro/stt/v1/audio/speech -o test.pcm`
