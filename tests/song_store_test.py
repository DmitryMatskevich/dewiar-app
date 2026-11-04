import json

from songs.store import SongStore


def test_song_store_round_trip(tmp_path):
    store = SongStore(tmp_path / "songs")
    created = store.create("Правда ведь обидно", "Высоцкий", source="query")
    assert created["stage"] == "identify"
    assert store.list()[0]["title"] == "Правда ведь обидно"

    updated = store.patch(created["song_id"], {"stage": "lyrics", "stage_label": "Распознаём слова", "status": "review"}, 1)
    assert updated["revision"] == 2
    assert store.get(created["song_id"])["status"] == "review"
    assert json.loads((tmp_path / "songs" / "index.json").read_text(encoding="utf-8"))


def test_song_store_rejects_stale_revision(tmp_path):
    store = SongStore(tmp_path / "songs")
    song = store.create("Планы", "Клявин")
    store.patch(song["song_id"], {"artist": "Клявин"}, 1)
    try:
        store.patch(song["song_id"], {"artist": "другая версия"}, 1)
    except ValueError as exc:
        assert "изменилась" in str(exc)
    else:
        raise AssertionError("stale revision was accepted")
