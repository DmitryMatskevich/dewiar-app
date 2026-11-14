"""Private local archive of imported email with FTS search and explicit cleanup."""
from __future__ import annotations

import email
import hashlib
import mailbox
import os
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from email import policy
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

from email_cleanup import classify


def _decode(value):
    try:
        return str(make_header(decode_header(str(value or ""))))
    except Exception:
        return str(value or "")


def _text(part):
    raw = part.get_payload(decode=True)
    if raw is None:
        val = part.get_payload()
        return val if isinstance(val, str) else ""
    try:
        return raw.decode(part.get_content_charset() or "utf-8", "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def _bodies(msg, root):
    body, attachments, texts = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = _decode(part.get_filename())
        raw = part.get_payload(decode=True) or b""
        if name:
            digest = hashlib.sha256(raw).hexdigest()
            ext = Path(name).suffix[:16]
            out = root / "attachments" / digest[:2] / (digest + ext)
            out.parent.mkdir(parents=True, exist_ok=True)
            if not out.exists():
                out.write_bytes(raw)
            attachments.append(f"{name} [{out}]")
            if part.get_content_type() == "text/plain":
                texts.append(_text(part))
            elif part.get_content_type() == "application/pdf":
                try:
                    r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(out), "-"], capture_output=True, timeout=30)
                    if r.returncode == 0:
                        texts.append(r.stdout.decode("utf-8", "replace"))
                except (OSError, subprocess.TimeoutExpired):
                    pass
        elif part.get_content_type() == "text/plain":
            body.append(_text(part))
        elif part.get_content_type() == "text/html":
            import re
            body.append(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _text(part))))
    return "\n".join(body), "\n".join(texts), "\n".join(attachments)


class EmailArchive:
    def __init__(self, state_dir):
        self.root = Path(state_dir) / "email_archive"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "archive.sqlite3"
        self.lock = threading.RLock()
        with self._db() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY, dedup_key TEXT UNIQUE, message_id TEXT, date TEXT,
                sender TEXT, recipients TEXT, cc TEXT, subject TEXT, body TEXT,
                attachment_text TEXT, attachment_names TEXT, raw_path TEXT, source TEXT);
              CREATE TABLE IF NOT EXISTS imports(source TEXT PRIMARY KEY, imported INTEGER, duplicates INTEGER, errors INTEGER);
              CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(subject,sender,recipients,body,attachment_text);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def status(self):
        with self._db() as db:
            total = db.execute("SELECT count(*) FROM messages").fetchone()[0]
            imports = [dict(r) for r in db.execute("SELECT source,imported,duplicates,errors FROM imports ORDER BY source")]
        return {"folder": str(self.root), "messages": total, "imports": imports}

    def _insert(self, db, msg, source, ordinal, raw_bytes=None):
        subj, sender = _decode(msg.get("Subject")), _decode(msg.get("From"))
        to, cc = _decode(msg.get("To")), _decode(msg.get("Cc"))
        body, atxt, anames = _bodies(msg, self.root)
        mid = str(msg.get("Message-ID") or "").strip()
        raw_bytes = raw_bytes if raw_bytes is not None else msg.as_bytes(policy=policy.SMTP)
        rawhash = hashlib.sha256(raw_bytes).hexdigest()
        canonical = hashlib.sha256("\0".join((subj.lower(), sender.lower(), to.lower(), body)).encode("utf-8", "replace")).hexdigest()
        key = f"mid:{mid.lower()}:{canonical}" if mid else f"raw:{rawhash}"
        try:
            date = parsedate_to_datetime(msg.get("Date")).isoformat()
        except Exception:
            date = str(msg.get("Date") or "")
        rawpath = self.root / "messages" / rawhash[:2] / (rawhash + ".eml")
        rawpath.parent.mkdir(parents=True, exist_ok=True)
        if not rawpath.exists():
            rawpath.write_bytes(raw_bytes)
        cur = db.execute("INSERT OR IGNORE INTO messages(dedup_key,message_id,date,sender,recipients,cc,subject,body,attachment_text,attachment_names,raw_path,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (key, mid, date, sender, to, cc, subj, body, atxt, anames, str(rawpath), f"{source}#{ordinal}"))
        if not cur.rowcount:
            return False
        db.execute("INSERT INTO search(subject,sender,recipients,body,attachment_text) VALUES(?,?,?,?,?)", (subj, sender, to, body, atxt + "\n" + anames))
        return True

    @staticmethod
    def _mailboxes(path):
        p = Path(path).expanduser().resolve()
        if p.is_file():
            return [p] if p.name == "mbox" or p.suffix.lower() in (".mbox", ".mbx") else []
        files = sorted(x for x in p.rglob("mbox") if x.is_file())
        return files or sorted(x for x in p.rglob("*.mbox") if x.is_file())

    def import_path(self, value):
        path = Path(os.path.expanduser(str(value or ""))).resolve()
        if not path.exists():
            raise ValueError("Путь импорта не найден")
        if path.is_dir() and (path / "archive.sqlite3").exists():
            return self._import_archive_db(path / "archive.sqlite3")
        boxes = self._mailboxes(path)
        if not boxes:
            raise ValueError("Не найден файл MBOX или архив archive.sqlite3")
        result = {"imported": 0, "duplicates": 0, "errors": 0}
        for boxpath in boxes:
            source = str(boxpath)
            with self.lock, self._db() as db:
                if db.execute("SELECT 1 FROM imports WHERE source=?", (source,)).fetchone():
                    result["duplicates"] += 1
                    continue
                box = mailbox.mbox(boxpath, create=False)
                for n, msg in enumerate(box, 1):
                    try:
                        if self._insert(db, msg, source, n): result["imported"] += 1
                        else: result["duplicates"] += 1
                    except Exception:
                        result["errors"] += 1
                box.close()
                db.execute("INSERT OR REPLACE INTO imports VALUES(?,?,?,?)", (source, result["imported"], result["duplicates"], result["errors"]))
        return {**result, **self.status()}

    def _import_archive_db(self, source_db):
        source_db = Path(source_db).resolve()
        result = {"imported": 0, "duplicates": 0, "errors": 0}
        src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
        with self.lock, self._db() as dst:
            source = str(source_db)
            if dst.execute("SELECT 1 FROM imports WHERE source=?", (source,)).fetchone():
                return {**result, "duplicates": 1, **self.status()}
            rows = src.execute("SELECT message_id,date,sender,recipients,cc,subject,body,attachment_text,raw_path FROM messages ORDER BY date")
            for n, row in enumerate(rows, 1):
                try:
                    rp = Path(row["raw_path"] or "")
                    raw = rp.read_bytes() if rp.is_file() else b""
                    msg = email.message_from_bytes(raw) if raw else email.message_from_string(
                        f"From: {row['sender'] or ''}\nTo: {row['recipients'] or ''}\nCc: {row['cc'] or ''}\nSubject: {row['subject'] or ''}\nDate: {row['date'] or ''}\nMessage-ID: {row['message_id'] or ''}\n\n{row['body'] or ''}")
                    # Preserve already-extracted archive content if reconstruction was necessary.
                    if self._insert(dst, msg, str(source_db), n, raw or None):
                        result["imported"] += 1
                        if row["attachment_text"]:
                            dst.execute("UPDATE messages SET attachment_text=? WHERE id=last_insert_rowid()", (row["attachment_text"],))
                    else: result["duplicates"] += 1
                except Exception: result["errors"] += 1
            dst.execute("INSERT OR REPLACE INTO imports VALUES(?,?,?,?)", (source, result["imported"], result["duplicates"], result["errors"]))
        src.close()
        return {**result, **self.status()}

    def search(self, query, limit=30):
        query = str(query or "").strip()
        if not query:
            return []
        with self._db() as db:
            rows = db.execute("SELECT m.id,m.date,m.sender,m.recipients,m.subject,m.body,m.raw_path,m.source FROM search s JOIN messages m ON m.id=s.rowid WHERE search MATCH ? ORDER BY m.date DESC LIMIT ?", (query, min(max(int(limit), 1), 100))).fetchall()
        return [dict(r) for r in rows]

    def cleanup_preview(self):
        with self._db() as db:
            rows = db.execute("SELECT id,message_id,sender,subject,date,raw_path FROM messages").fetchall()
        counts = {}; matched = 0
        for row in rows:
            try: msg = email.message_from_bytes(Path(row["raw_path"]).read_bytes())
            except Exception: continue
            reasons = classify(msg)
            if reasons: matched += 1
            for reason in reasons: counts[reason] = counts.get(reason, 0) + 1
        return {"total": matched, "by_reason": counts}

    def cleanup(self):
        with self.lock, self._db() as db:
            rows = db.execute("SELECT id,message_id,sender,subject,date,raw_path FROM messages").fetchall()
            remove = []
            for row in rows:
                try: msg = email.message_from_bytes(Path(row["raw_path"]).read_bytes())
                except Exception: continue
                reasons = classify(msg)
                if reasons: remove.append((row, reasons))
            report = self.root / "cleanup-report.csv"
            import csv
            with report.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f); w.writerow(["message_id", "reasons", "sender", "subject", "date"])
                for row, reasons in remove: w.writerow([row["message_id"], "; ".join(reasons), row["sender"], row["subject"], row["date"]])
            for row, _ in remove:
                # FTS5 has no in-place delete without stored column values; rebuild below.
                db.execute("DELETE FROM messages WHERE id=?", (row["id"],))
                try: Path(row["raw_path"]).unlink(missing_ok=True)
                except OSError: pass
            # Drop orphaned content-addressed attachments after removing the indexed messages.
            used = set()
            for (names,) in db.execute("SELECT attachment_names FROM messages"):
                for match in __import__("re").finditer(r"\[([^\]]+)\]", names or ""):
                    used.add(str(Path(match.group(1)).resolve()))
            for path in (self.root / "attachments").rglob("*"):
                if path.is_file() and str(path.resolve()) not in used:
                    try: path.unlink()
                    except OSError: pass
            db.execute("DELETE FROM search")
            for row in db.execute("SELECT id,subject,sender,recipients,body,attachment_text FROM messages").fetchall():
                db.execute("INSERT INTO search(rowid,subject,sender,recipients,body,attachment_text) VALUES(?,?,?,?,?,?)", tuple(row))
        return {"removed": len(remove), "report": str(report), **self.status()}
