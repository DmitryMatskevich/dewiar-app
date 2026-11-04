"""Search regressions: broadened YouTube queries, Yandex handoff and chord/text sources."""
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import tools  # noqa: E402
from songs import sources  # noqa: E402


def test_discover_expands_and_ranks_song_sources():
    calls = []

    class YoutubeDL:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def extract_info(self, url, download=False):
            calls.append(url)
            return {"entries": [
                {"id": "5ab3wVwfwr8", "title": "Девочке три, она едет у папы на шее",
                 "channel": "ZZarubin", "duration": 180},
                {"id": "XQJq14A2RUw", "title": "Девочке три (live)",
                 "channel": "ZZarubin", "duration": 181},
                {"id": "AAAAAAAAAAA", "title": "Девочке три, она едет у папы на шее",
                 "channel": "Another Artist", "duration": 180},
            ]}

    with patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=YoutubeDL)}), \
         patch.object(sources, "urlopen", return_value=BytesIO(b"[]")):
        candidates, errors = sources.discover(
            "Девочке три ZZarubin",
            ["https://662.amdm.ru/akkordi/zzarubin/217158/devochke_tri_ona_edet_u_papy_na_shee/"],
        )

    assert not errors, errors
    assert "ytsearch10:Девочке три ZZarubin" in calls
    assert "ytsearch10:Девочке три Z Zarubin" in calls
    assert {c["candidate_id"] for c in candidates if c["kind"] == "youtube"} == {
        "youtube:5ab3wVwfwr8", "youtube:XQJq14A2RUw"}
    assert candidates[0]["candidate_id"] == "youtube:5ab3wVwfwr8"
    assert any(c["url"].startswith("yandexmusic://search?text=") for c in candidates)
    assert any("amdm.ru/akkordi" in c.get("url", "") for c in candidates)
    assert any("текст песни" in unquote(c.get("url", "")) for c in candidates)
    assert any(c.get("url", "").startswith("https://662.amdm.ru/") for c in candidates)

    with patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=YoutubeDL)}), \
         patch.object(sources, "urlopen", return_value=BytesIO(b"[]")):
        known_artist, _ = sources.discover("Девочке три ZZarubin", title="Девочке три", artist="ZZarubin")
    assert "youtube:AAAAAAAAAAA" not in {c["candidate_id"] for c in known_artist}


def test_current_yandex_track_uses_accessibility_tree():
    fake = SimpleNamespace(
        permission=lambda: {"granted": True},
        running=lambda: [{"id": "ru.yandex.desktop.music", "name": "Яндекс Музыка"}],
        windows=lambda _app: (None, [{"number": 1}]),
        execute=lambda _args: {"tree": {"role": "AXGroup", "children": [
            {"role": "AXStaticText", "value": "ZZarubin — Девочке три, она едет у папы на шее"},
        ]}},
    )
    with patch.object(tools, "MAC", True), patch.dict(sys.modules, {"mac_control": fake}):
        result = tools.media_track_info()
    assert result == {"title": "Девочке три, она едет у папы на шее", "artist": "ZZarubin"}


if __name__ == "__main__":
    test_discover_expands_and_ranks_song_sources()
    test_current_yandex_track_uses_accessibility_tree()
    print("OK: song sources and Yandex Music current-track discovery")
