# Локальный модуль песен

Работает отдельным процессом Python 3.12. Основной `.venv`, настройки LLM/STT/TTS и каталоги существующих голосов не изменяются. Для вывода интерфейса runtime не нужен; для полной обработки нужны заранее установленные веса.

## Установка на этом Mac

В отдельном окружении установить `requirements-songs.txt`. Рабочий путь:

`~/Library/Application Support/Dewiar/runtimes/songs/bin/python`

Явный установщик весов: `tools/install_song_models.py`. Он сохраняет ревизии и пути в `work/song-verification/model-install.json`; для установленной `.app` манифест копируется в `~/Library/Application Support/Dewiar/runtimes/songs-models.json`.

Установлены Whisper large-v2 MLX, Qwen3-ASR 0.6B и Qwen3 ForcedAligner 0.6B. GigaAM использует существующие файлы Dewiar. Worker работает с offline-флагами и запретом записи bytecode в подписанную `.app`. Установка весов не является частью открытия вкладки/распознавания.

Для Chordino выполнить `tools/install_song_chordino.py` Python 3.12 после установки инструментов clang++, cmake, Homebrew boost и libsndfile. Это отдельный явный шаг. Скрипт проверяет закреплённые Git revisions, собирает CLI и сохраняет исходники с лицензиями в `runtimes/chordino/sources`. Исполняемый файл — `runtimes/chordino/bin/chordextract`, манифест содержит SHA256. Применяется встроенный словарь Chordino; произвольный системный chord.dict не подхватывается.

Источники:
- [Qwen3-ASR и выравниватель](https://github.com/QwenLM/Qwen3-ASR).
- [Whisper MLX](https://github.com/ml-explore/mlx-examples/tree/main/whisper).
- [Chordino / NNLS Chroma](https://github.com/c4dm/nnls-chroma), GPL-2.0-or-later, revision `4c5f214a75cb354f8d8c933e161377c5b4d83713`.
- [Vamp SDK](https://github.com/vamp-plugins/vamp-plugin-sdk), revision `44c2487763eb248a933e9eff9169cfadee375009`.

Chordino выбран как дополнительный готовый алгоритм с NNLS-признаками и басом. Чужие оценки качества не переносятся на пользовательский набор. Он также ошибается на пении и старых записях; результат остаётся предварительным. Без бинарника либо при его ошибке используется CQT/Viterbi, причина резерва сохраняется в результате.

HTDemucs проверялся отдельно в `.songs-separation-venv` с совместимыми torch/torchaudio. Это эксперимент сравнения, а не автоматический новый вход для каждого распознавателя. Не понижать torch основного музыкального окружения ради torchaudio.

## Проверки

Из каталога `app`: `.venv/bin/python tools/test_songs_safe.py --report work/song-verification/tests-final.json`.

UI без моделей/настроек: `.venv/bin/python tools/songs_ui_preview.py`. Он создаёт временное хранилище и маркирует тестовые данные.

После установки: проверить список, открыть карточку, выполнить реальный разбор, прослушать, сверить настройки и `codesign --verify --deep --strict /Applications/Dewiar.app`. Проверка только исходников не заменяет проверку установленного приложения.

## Итоговый лист и воспроизведение

В «Задачи → Песни» аккорды выводятся над текстом моноширинным шрифтом, с транспозицией на полтона, копированием чистого текста, до трёх ссылок на источники и проигрыванием сохранённой записи. Пауза сохраняет позицию; остановка сбрасывает её. Транспозиция меняет обозначения аккордов, а не звук записи.

Перенос строки или начало следующей фразы не повторяет уже звучащий аккорд. Исходные события, времена, ручные привязки и текст сохраняются. Возврат к аккорду после другого аккорда или тишины остаётся видимым. Это же представление используется для копирования и ChordPro.

Дополнительные проверки интерфейса: `node tests/song_sheet_js_test.cjs` и `node tests/song_playback_js_test.cjs`.
