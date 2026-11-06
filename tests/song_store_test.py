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


def test_song_store_processing_filters_include_active_and_stopped_jobs(tmp_path):
    store = SongStore(tmp_path / "songs")
    active = store.create("Активная", "Исполнитель")
    waiting = store.create("Ожидает выбора", "Исполнитель")
    stopped = store.create("Остановилась", "Исполнитель")
    ready = store.create("Готовая", "Исполнитель")
    store.patch(active["song_id"], {"status": "running"}, active["revision"])
    store.patch(waiting["song_id"], {"status": "needs_input"}, waiting["revision"])
    store.patch(stopped["song_id"], {"status": "interrupted"}, stopped["revision"])
    store.patch(ready["song_id"], {"status": "ready"}, ready["revision"])

    assert {row["title"] for row in store.list(status="batch")} == {"Активная", "Ожидает выбора", "Остановилась"}
    assert {row["title"] for row in store.list(status="stopped")} == {"Остановилась"}


def test_song_store_delete_removes_owned_song_folder(tmp_path):
    store = SongStore(tmp_path / "songs")
    song = store.create("Удаляемая", "Исполнитель")
    folder = store.root / song["song_id"]
    (folder / "audio").mkdir()
    (folder / "audio" / "recording.wav").write_bytes(b"audio")
    (folder / "karaoke").mkdir()
    (folder / "karaoke" / "manifest.json").write_text("{}", encoding="utf-8")
    assert store.delete(song["song_id"])
    assert not folder.exists()
    assert store.list() == []
