"""Read the labels of the parts («Припев:», «Куплет 2:») from confirmed text sources once.

Sources saved before 14.11 kept the sung text only. The page is read again;
its labels are stored only when the page still gives exactly the saved text
(the line numbers of the labels refer to it). Cards in processing are skipped.
Run with the app closed: ``.venv/bin/python tools/songs_reference_labels.py --apply``.
"""
import argparse
import copy
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'core'))
from songs.store import SongStore
from songs.reference_fragments import lyrics_with_labels
from songs.text_reference import fetch_source

ROOT = Path.home() / 'Library/Application Support/Dewiar/state/songs'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    store = SongStore(args.root)
    pages = {}
    for path in sorted(args.root.glob('*/song.json')):
        row = store.get(path.parent.name)
        if not row or row.get('status') in {'running', 'recording'} or row.get('active_run_id'):
            continue
        report = copy.deepcopy(row.get('text_reference') or {})
        changed = 0
        for ref in report.get('references') or []:
            url = str(ref.get('url') or '')
            if 'labels' in ref or not ref.get('plain_lyrics') or not url.startswith(('http://', 'https://')) \
                    or 'youtube.com' in url or ref.get('manual_source'):
                continue
            if url not in pages:
                try:
                    page = fetch_source(url)
                    pages[url] = lyrics_with_labels(page.get('extracted_text', '')) if page.get('status') == 'available' else None
                except Exception as exc:
                    pages[url] = None
                    print(f'— {row.get("title")}: {url}: {str(exc)[:80]}')
            got = pages[url]
            if not got:
                continue
            text, labels = got
            if text.strip() != str(ref['plain_lyrics']).strip():
                print(f'— {row.get("title")}: страница изменилась, подписи не взяты ({url[:60]})')
                continue
            ref['labels'] = labels
            changed += 1
            print(f'✓ {row.get("title")}: {len(labels)} подписей ({url[:60]})')
        if changed and args.apply:
            store.patch(row['song_id'], {'text_reference': report})


if __name__ == '__main__':
    main()
