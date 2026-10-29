#!/usr/bin/env python3
"""Комментарии под видео YouTube — в один файл, бесплатно (18.10: «кредиты что, нужно покупать?» — коннектор TubeAlfred
берёт по 5 кредитов за страницу). yt-dlp читает публичные комментарии и ответы так же, как браузер, без ключей и входа.

    python3 tools/yt_comments.py https://www.youtube.com/watch?v=lXRiHj9j9Pk > комментарии.md

из папки исходников (архив dewiar-source-X.zip). Нужен yt-dlp: с 0.38.79 он ставится вместе с Dewiar — тогда /opt/dewiar/venv/bin/python
вместо python3, — или pip install --user yt-dlp. На выходе — Markdown: автор, время, лайки, текст, ответы
под своим комментарием, новые сверху. Файл можно отдать Claude — разобрать по частотности и написать черновики ответов.
--json — то же в JSON."""
import argparse
import datetime as dt
import json
import sys


def fetch(url, limit=2000):
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "getcomments": True,
            "extractor_args": {"youtube": {"max_comments": [str(limit), "all", "all", "all"], "comment_sort": ["new"]}}}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=False) or {}
    return info


def thread(info):
    """-> [{id, author, when, likes, text, creator, replies: [...]}] — верхние комментарии с ответами, новые сверху."""
    top, by = [], {}
    for c in info.get("comments") or []:
        item = {"id": c.get("id"), "author": c.get("author") or "", "when": c.get("timestamp") or 0, "likes": c.get("like_count") or 0,
                "text": (c.get("text") or "").strip(), "creator": bool(c.get("author_is_uploader")), "replies": []}
        parent = c.get("parent") or "root"
        if parent == "root":
            top.append(item); by[item["id"]] = item
        else:
            by.setdefault(parent, {"replies": []})["replies"].append(item)
    top.sort(key=lambda x: -x["when"])
    for t in top:
        t["replies"].sort(key=lambda x: x["when"])
    return top


def markdown(info, top):
    when = lambda ts: dt.datetime.fromtimestamp(ts).strftime("%d.%m %H:%M") if ts else ""  # noqa: E731
    n = sum(1 + len(t["replies"]) for t in top)
    out = [f"# Комментарии: {info.get('title', '')}", "",
           f"{info.get('webpage_url', '')} · просмотров {info.get('view_count', '?')} · лайков {info.get('like_count', '?')} · комментариев {n}", ""]
    for t in top:
        mark = " (автор)" if t["creator"] else ""
        out.append(f"## {t['author']}{mark} — {when(t['when'])}, ♥ {t['likes']}")
        out.append(t["text"]); out.append("")
        for r in t["replies"]:
            out.append(f"> **{r['author']}{' (автор)' if r['creator'] else ''}** — {when(r['when'])}: " + r["text"].replace("\n", "\n> "))
        if t["replies"]:
            out.append("")
    unanswered = [t for t in top if not t["creator"] and not any(r["creator"] for r in t["replies"])]
    out.append(f"---\nБез вашего ответа: {len(unanswered)} — " + ", ".join(t["author"] for t in unanswered))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=2000)
    a = ap.parse_args()
    try:
        info = fetch(a.url, a.limit)
    except ImportError:
        sys.exit("нужен yt-dlp: pip install yt-dlp (или запустите питоном Dewiar: /opt/dewiar/venv/bin/python)")
    top = thread(info)
    print(json.dumps({"title": info.get("title"), "url": info.get("webpage_url"), "comments": top}, ensure_ascii=False, indent=1)
          if a.json else markdown(info, top))


if __name__ == "__main__":
    main()
