"""Разбиение встречи по участникам (диаризация): кто и когда говорил.

Сегментация pyannote segmentation-3.0 (ONNX, MIT) + модель голоса CAM++ (тот же файл, что у профиля владельца
в speaker.py) + кластеризация sherpa-onnx. Движок sherpa-onnx загружается только в отдельном процессе
(worker.py, «Dewiar --diarize-worker»): ядро его не импортирует. Без токенов и учётных записей.
План: plan/meeting-diarization-plan-2026-11-06.md.
"""

MODES = ("none", "owner", "participants")
DEFAULT_MODE = "owner"


def normal_mode(value):
    """none — без подписей, owner — «Вы / Собеседник» (как раньше), participants — «Вы» + «Участник N»."""
    value = str(value or "").strip().lower()
    return value if value in MODES else DEFAULT_MODE
