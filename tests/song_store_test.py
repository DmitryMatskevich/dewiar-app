import json

from songs.store import SongStore, TAG_OPTIONS
from songs.tags import infer_tags


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


def test_song_store_ratings_round_trip_and_filter(tmp_path):
    store = SongStore(tmp_path / "songs")
    five = store.create("Пять звёзд", "Автор")
    three = store.create("Три звезды", "Автор")
    store.patch(five["song_id"], {"rating": 5}, five["revision"])
    store.patch(three["song_id"], {"rating": 3}, three["revision"])

    assert store.get(five["song_id"])["rating"] == 5
    assert store.list(compact=True)[0]["rating"] == 3
    assert [row["title"] for row in store.list(rating=5)] == ["Пять звёзд"]
    assert [row["title"] for row in store.list(rating="3")] == ["Три звезды"]
    try:
        store.patch(three["song_id"], {"rating": 6})
    except ValueError as exc:
        assert "от 0 до 5" in str(exc)
    else:
        raise AssertionError("rating outside 0..5 was accepted")


def test_song_store_supports_multiple_tag_filters(tmp_path):
    store = SongStore(tmp_path / "songs")
    love = store.create("Любовь", "Автор")
    rock = store.create("Рок", "Автор")
    other = store.create("Другое", "Автор")
    store.patch(love["song_id"], {"tags": ["Любовь"]}, love["revision"])
    store.patch(rock["song_id"], {"tags": ["Рок"]}, rock["revision"])
    store.patch(other["song_id"], {"tags": ["Юмор"]}, other["revision"])

    assert {row["title"] for row in store.list(tags=["Любовь", "Рок"])} == {"Любовь", "Рок"}
    assert {row["title"] for row in store.list(tag=["Любовь", "Рок"])} == {"Любовь", "Рок"}


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


def test_song_store_searches_inside_lyrics(tmp_path):
    store = SongStore(tmp_path / "songs")
    first = store.create("Первая песня", "Автор")
    second = store.create("Вторая песня", "Автор")
    store.patch(first["song_id"], {"phrases": [{"text": "Я молчу и улыбаюсь"}]}, first["revision"])
    store.patch(second["song_id"], {"phrases": [{"text": "День за днём одинокий груз"}]}, second["revision"])

    assert [row["title"] for row in store.list(lyrics_query="улыбаюсь")] == ["Первая песня"]
    assert store.list(lyrics_query="несуществующая фраза") == []


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


def test_song_store_accepts_rock_tag_and_infers_it(tmp_path):
    store = SongStore(tmp_path / "songs")
    song = store.create("Рок-песня", "Группа")
    updated = store.patch(song["song_id"], {"tags": ["Рок"]}, song["revision"])
    assert updated["tags"] == ["Рок"]
    tags, scores = infer_tags([{"text": "Мы играем рок и метал, гитары звучат"}])
    assert "Рок" in tags
    assert scores["Рок"] >= 0.70


def test_removed_song_tags_are_not_allowed_or_inferred():
    removed = {"Холодные", "Детские", "Италия", "Запад", "Военные", "Советские", "Из фильмов"}
    assert not (removed & set(TAG_OPTIONS))
    tags, scores = infer_tags([{"text": "Детская военная песня из советского фильма, западная Италия"}])
    assert not (removed & set(tags))
    assert not (removed & set(scores))
