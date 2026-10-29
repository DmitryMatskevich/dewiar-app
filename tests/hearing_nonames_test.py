"""Слух без модели имён (CI и отчёты 19.10: модель Vosk не скачалась — сайт закрыт или медленный интернет при первом
запуске — и не работала даже кнопка: «Удержание слишком короткое (0.0 с)»). Та же проверка диктовки удержанием
(hold_dictation_test), но модель имён «не скачивается» (DEWIAR_VOSK_OFF=1): кнопка, удержание, «Патрик, …» в начале
фразы, выключенный микрофон — всё работает; имя голосом включится, когда модель докачается."""
import os, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
r = subprocess.run([sys.executable, str(HERE / "hold_dictation_test.py")], env=dict(os.environ, DEWIAR_VOSK_OFF="1"), capture_output=True, text=True)
print(r.stdout[-3000:])
print("\nИТОГ:", "всё прошло (без модели имён)" if r.returncode == 0 else "не прошло — без модели имён слух не работает")
sys.exit(r.returncode)
