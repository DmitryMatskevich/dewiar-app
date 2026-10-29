# Библиотеки Microsoft Visual C++ (msvcp140 и др.)

onnxruntime (слух: Silero VAD, GigaAM) требует msvcp140.dll и msvcp140_1.dll. В Python они не входят
(там только vcruntime140*.dll), а на «чистой» Windows 10 без пакета Visual C++ Redistributable их нет —
ядро падает при запуске. Поэтому кладём их рядом с python.exe (локальное развёртывание, разрешённое
лицензией Visual C++ Redistributable).

Версия 14.44.35211 — та же, что у vcruntime140.dll в Python. Источник: колесо PyPI `msvc-runtime` (win_amd64).
Обновить: `uv pip install --target t --python-platform x86_64-pc-windows-msvc --python-version 3.12 msvc-runtime`.
