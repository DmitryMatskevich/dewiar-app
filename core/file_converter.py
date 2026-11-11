"""Local, deterministic file conversion helpers used by native background tasks.

The module deliberately uses the standard library, Pillow (already required), the
application's existing imageio-ffmpeg binary, and optional LibreOffice. No model
is involved. Conversion is conservative: only declared format families are
accepted and an output is always written to the caller's task output directory.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import ipaddress
import os
import posixpath
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET


class ConversionError(ValueError):
    """A user-correctable conversion problem."""


TARGET_ALIASES = {
    "word": "docx", "документ": "docx", "док": "docx", "doc": "docx",
    "excel": "xlsx", "эксель": "xlsx", "таблица": "xlsx", "spreadsheet": "xlsx",
    "markdown": "md",
    "текст": "txt", "text": "txt", "веб-страница": "html", "вебстраница": "html",
    "изображение": "png", "картинка": "png", "фото": "png",
    "jpeg": "jpg", "jpg": "jpg", "svg": "svg",
    "мп3": "mp3", "эмпэтри": "mp3", "мпег": "mp3", "мпэг": "mp3",
    "вэйв": "wav", "вэ-вэ": "wav",
}

TARGETS = {"txt", "md", "html", "htm", "docx", "pdf", "xlsx", "csv", "tsv",
           "png", "jpg", "jpeg", "svg", "gif", "webp", "bmp", "tif", "tiff",
           "wav", "mp3", "mpeg", "m4a", "aac", "flac", "ogg", "opus"}

TEXT_EXTS = {"txt", "text", "md", "markdown", "html", "htm", "csv", "tsv", "json", "xml", "log", "srt", "vtt"}
OFFICE_EXTS = {"doc", "docx", "odt", "rtf", "xls", "xlsx", "ods", "ppt", "pptx", "odp"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff"}
AUDIO_EXTS = {"wav", "mp3", "mpeg", "m4a", "aac", "flac", "ogg", "opus", "aiff", "wma"}
VIDEO_EXTS = {"mp4", "mov", "mkv", "avi", "webm", "m4v", "mpeg", "mpg"}
MEDIA_OUT = {"wav", "mp3", "mpeg", "m4a", "aac", "flac", "ogg", "opus"}
IMAGE_FORMAT = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "gif": "GIF", "webp": "WEBP",
                "bmp": "BMP", "tif": "TIFF", "tiff": "TIFF"}
XML_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def normalise_target(value: str) -> str:
    target = str(value or "").strip().lower().lstrip(".")
    target = re.sub(r"\s+", " ", target)
    target = TARGET_ALIASES.get(target, target)
    if target == "markdown":
        target = "md"
    if target == "excel":
        target = "xlsx"
    if target == "word":
        target = "docx"
    if target not in TARGETS:
        raise ConversionError(f"Неизвестный целевой формат «{value}»")
    return target


def requested_target(text: str) -> str | None:
    """Return a format only for an explicit conversion command."""
    source = str(text or "").strip().lower().replace("ё", "е")
    if not re.search(r"\b(?:конвертир\w*|преобраз\w*)\b", source):
        return None
    # Prefer a format after a preposition; a format name elsewhere still counts
    # for natural voice forms such as «Лира, в mp3 преобразуй».
    aliases = {
        "docx": ("docx", "word", "ворд", "документ word", "документ ворд"),
        "xlsx": ("xlsx", "excel", "эксель", "excel-файл", "таблицу excel", "формат excel"),
        "md": ("markdown", "маркдаун", "md"),
        "txt": ("txt", "text", "текстовый файл", "в текст", "текст"),
        "html": ("html", "htm", "html-файл", "в html"),
        "pdf": ("pdf", "пдф"), "csv": ("csv", "цсв"), "tsv": ("tsv",),
        "png": ("png", "пнг"), "gif": ("gif", "гиф"), "jpg": ("jpg", "jpeg", "джипег", "джейпег"),
        "svg": ("svg", "эсвэгэ", "в svg", "векторный svg"),
        "webp": ("webp",), "bmp": ("bmp",), "tiff": ("tiff", "tif"),
        "wav": ("wav", "в wav", "вэйв", "в wav-формат"), "mp3": ("mp3", "мп3", "mpeg", "мпег", "мпэг"),
        "m4a": ("m4a",), "aac": ("aac",), "flac": ("flac",), "ogg": ("ogg",), "opus": ("opus",),
    }
    # The last format mentioned is usually the destination («Excel в CSV»).
    # Prefer an explicit format after «в» when present, otherwise use the final
    # recognised format token in the request.
    matches = []
    for target, words in aliases.items():
        for word in words:
            for match in re.finditer(r"(?<![\w])" + re.escape(word) + r"(?![\w])", source):
                matches.append((match.end(), target))
    return max(matches)[1] if matches else None


def is_conversion_command(text: str) -> bool:
    return bool(re.search(r"\b(?:конвертир\w*|преобраз\w*)\b", str(text or "").lower().replace("ё", "е")))


def is_text_extraction_command(text: str) -> bool:
    """Recognise explicit local OCR/text extraction requests before an LLM turn."""
    value = str(text or "").lower().replace("ё", "е")
    return bool(re.search(r"\b(?:извлеч\w*\s+текст|распозн\w*\s+текст|вытащ\w*\s+текст|прочит\w*\s+текст|получ\w*\s+текст|ocr)\b", value))


def is_contact_extraction_command(text: str) -> bool:
    """Recognise a deliberate request to extract contact details from an attached image."""
    value = str(text or "").lower().replace("ё", "е")
    verb = r"(?:извлеч\w*|получ\w*|найд\w*|определ\w*|прочит\w*|extract|get|find)"
    contact = r"(?:контакт\w*|e[ -]?mail|email|телефон\w*|почт\w*)"
    return bool(re.search(rf"\b{verb}\b.{{0,40}}\b{contact}\b|\b{contact}\b.{{0,40}}\b{verb}\b", value, re.I))


def webpage_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s<>\"']+", str(text or ""), re.I)
    if not match: return None
    return match.group(0).rstrip(".,;:!?)»]")


def _validate_public_web_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ConversionError("Укажите публичную ссылку на страницу HTTP или HTTPS")
    host = parsed.hostname.rstrip(".").encode("idna").decode("ascii")
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ConversionError(f"Не удалось найти адрес сайта: {exc}") from exc
    if not addresses or any(not ipaddress.ip_address(address.split("%", 1)[0]).is_global for address in addresses):
        raise ConversionError("Ссылки на локальные и внутренние адреса запрещены")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def fetch_webpage(url: str, dest: str | Path, *, cancel=None) -> Path:
    """Fetch one public HTML page safely, without cookies, proxies, scripts or model calls."""
    current = _validate_public_web_url(url)
    no_redirect = type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {"redirect_request": lambda self, req, fp, code, msg, headers, newurl: None})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), no_redirect())
    for _ in range(4):
        if cancel is not None and cancel.is_set(): raise ConversionError("Загрузка страницы отменена")
        request = urllib.request.Request(current, headers={"User-Agent": "Dewiar/1.0 (local document conversion)", "Accept": "text/html,application/xhtml+xml"})
        try:
            response = opener.open(request, timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                current = _validate_public_web_url(urllib.parse.urljoin(current, exc.headers["Location"]))
                continue
            raise ConversionError(f"Сайт вернул HTTP {exc.code}") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ConversionError(f"Не удалось загрузить страницу: {exc}") from exc
        with response:
            content_type = response.headers.get_content_type().lower()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise ConversionError(f"Ссылка ведёт на {content_type}, а нужна HTML-страница")
            length = int(response.headers.get("Content-Length") or 0)
            if length > 10 * 1024 * 1024: raise ConversionError("HTML-страница больше 10 МБ")
            data = response.read(10 * 1024 * 1024 + 1)
            if len(data) > 10 * 1024 * 1024: raise ConversionError("HTML-страница больше 10 МБ")
            encoding = response.headers.get_content_charset() or "utf-8"
            try: value = data.decode(encoding, errors="replace")
            except LookupError: value = data.decode("utf-8", errors="replace")
            if "<html" not in value[:20000].lower() and "<!doctype html" not in value[:20000].lower():
                raise ConversionError("Полученный ответ не похож на HTML-страницу")
            if not re.search(r"<base\b", value, re.I):
                base = '<base href="' + html.escape(current, quote=True) + '">'
                head = re.search(r"<head\b[^>]*>", value, re.I)
                if head: value = value[:head.end()] + base + value[head.end():]
                else:
                    page = re.search(r"<html\b[^>]*>", value, re.I)
                    if page: value = value[:page.end()] + "<head>" + base + "</head>" + value[page.end():]
                    else: value = "<head>" + base + "</head>" + value
            target = Path(dest); target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding="utf-8")
            return target
    raise ConversionError("Слишком много перенаправлений страницы")


def _ext(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def targets_for(path: str | Path) -> list[str]:
    """Conservative target choices for a file's known extension."""
    ext = _ext(Path(path))
    if ext in IMAGE_EXTS:
        return ["png", "jpg", "jpeg", "svg", "gif", "webp", "bmp", "tiff", "pdf"]
    if ext in AUDIO_EXTS or ext in VIDEO_EXTS:
        return sorted(MEDIA_OUT)
    if ext == "xlsx":
        return ["csv", "tsv", "html", "pdf", "txt", "md", "docx"]
    if ext in {"csv", "tsv"}:
        return ["xlsx", "csv", "tsv", "txt", "md", "html", "docx", "pdf"]
    if ext == "pdf":
        return ["txt", "md", "html", "docx"]
    if ext == "docx":
        return ["txt", "md", "html", "xlsx", "csv", "pdf"]
    if ext in TEXT_EXTS:
        return ["txt", "md", "html", "docx", "xlsx", "csv", "pdf"]
    if ext in OFFICE_EXTS:
        if ext in {"doc", "odt", "rtf", "xls", "ods"}:
            return ["txt", "html", "docx", "xlsx", "csv", "pdf"]
        return ["txt", "html", "pdf"]
    return []


def _safe_stem(path: Path) -> str:
    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", path.stem).strip(" ._")[:100]
    return stem or "converted"


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1251", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="replace")


class _PlainHTML(HTMLParser):
    BLOCK = {"p", "div", "section", "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br", "blockquote"}

    def __init__(self, markdown=False):
        super().__init__(convert_charrefs=True)
        self.markdown = markdown
        self.out: list[str] = []
        self.skip = 0
        self.href: list[str | None] = []
        self.bold = 0
        self.italic = 0
        self.list_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript", "svg", "iframe"}:
            self.skip += 1
            return
        if self.skip:
            return
        if self.markdown:
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self.out.append("\n\n" + "#" * int(tag[1]) + " ")
            elif tag in self.BLOCK:
                self.out.append("\n\n")
            elif tag == "li":
                self.out.append("\n- ")
            elif tag in {"strong", "b"}:
                self.out.append("**")
                self.bold += 1
            elif tag in {"em", "i"}:
                self.out.append("*")
                self.italic += 1
            elif tag == "a":
                self.href.append(attrs.get("href"))
                self.out.append("[")
            elif tag == "td":
                self.out.append(" | ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in {"p", "div", "h1", "h2", "h3", "h4", "li", "tr"}:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "iframe"}:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if self.markdown:
            if tag in {"strong", "b"} and self.bold:
                self.out.append("**")
                self.bold -= 1
            elif tag in {"em", "i"} and self.italic:
                self.out.append("*")
                self.italic -= 1
            elif tag == "a":
                href = self.href.pop() if self.href else None
                if href:
                    self.out.append("](" + href.replace(")", "%29") + ")")
                else:
                    self.out.append("]")
            elif tag in self.BLOCK:
                self.out.append("\n\n")
        elif tag in {"p", "div", "h1", "h2", "h3", "h4", "li", "tr"}:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def text(self):
        value = "".join(self.out)
        value = re.sub(r"[\t\xa0 ]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()


def _html_to_text(value: str, markdown=False) -> str:
    parser = _PlainHTML(markdown=markdown)
    parser.feed(value)
    return parser.text()


def _html_to_markdown(value: str) -> str:
    parser = _HTMLBlocks()
    parser.feed(value)
    parser.close()
    rendered, i = [], 0
    while i < len(parser.blocks):
        kind, content = parser.blocks[i]
        if kind == "table": rendered.append(_rows_markdown(content))
        elif kind == "image": rendered.append("![" + str(content.get("alt") or "Изображение").replace("]", "\\]") + "](" + str(content.get("src") or "").replace(")", "%29") + ")")
        elif kind == "float_image":
            side = content.get("float") if content.get("float") in {"left", "right"} else "left"
            width = str(content.get("width") or "").strip()
            if not re.fullmatch(r"(?:\d{1,3}(?:\.\d{1,2})?)(?:px|%|rem|em|vw|cm|mm|in)", width, re.I): width = ""
            style = f"float:{side};margin:0 14px 10px 0;{f'width:{width};' if width else 'width:40%;max-width:280px;'}"
            fragment = ['<div style="display:flow-root">', f'<img src="{html.escape(str(content.get("src") or ""), quote=True)}" alt="{html.escape(str(content.get("alt") or "Изображение"), quote=True)}" style="{style}">']
            j = i + 1
            while j < len(parser.blocks) and parser.blocks[j][0] in {"p", "li", "blockquote"}:
                next_kind, next_content = parser.blocks[j]
                text = html.escape(str(next_content))
                if next_kind == "li": text = "• " + text
                fragment.append(f"<p>{text}</p>")
                j += 1
            fragment.append("</div>")
            rendered.append("\n\n".join(fragment)); i = j - 1
        elif kind.startswith("h"): rendered.append("#" * int(kind[1:]) + " " + str(content))
        elif kind == "li": rendered.append("- " + str(content))
        elif kind == "pre": rendered.append("```\n" + str(content) + "\n```")
        else: rendered.append(str(content))
        i += 1
    return "\n\n".join(item for item in rendered if item.strip())


class _HTMLTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell, self.depth = [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "table": self.depth += 1
        if not self.depth: return
        if tag == "tr": self.row = []
        elif tag in {"td", "th"} and self.row is not None: self.cell = []

    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split())); self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row); self.row = None
        elif tag == "table": self.depth = max(0, self.depth - 1)


def _html_table_rows(value: str) -> list[list[str]]:
    parser = _HTMLTable()
    parser.feed(value)
    return parser.rows


def _markdown_html(value: str) -> str:
    """Small Markdown subset for headings, paragraphs, lists, emphasis and code."""
    value, float_blocks = _extract_markdown_float_html(value)
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, para, in_list, in_code = [], [], False, False

    def flush():
        nonlocal para
        if para:
            table_rows = _markdown_table_rows("\n".join(para).splitlines())
            if table_rows:
                width = max(map(len, table_rows))
                normal = [row + [""] * (width - len(row)) for row in table_rows]
                out.append("<table><thead><tr>" + "".join("<th>" + html.escape(cell) + "</th>" for cell in normal[0]) +
                           "</tr></thead><tbody>" + "".join("<tr>" + "".join("<td>" + html.escape(cell) + "</td>" for cell in row) + "</tr>" for row in normal[1:]) + "</tbody></table>")
                para = []
                return
            text = " ".join(x.strip() for x in para).strip()
            text = html.escape(text)
            text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
            text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', text)
            out.append("<p>" + text + "</p>")
            para = []

    for line in lines:
        if line.startswith("```"):
            flush()
            if in_list:
                out.append("</ul>")
                in_list = False
            if in_code:
                out.append("</code></pre>")
            else:
                out.append("<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line) + "\n")
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
        ordered = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if heading or bullet or ordered or not line.strip():
            flush()
            if in_list and not (bullet or ordered):
                out.append("</ul>")
                in_list = False
            if heading:
                n = len(heading.group(1))
                out.append(f"<h{n}>{html.escape(heading.group(2))}</h{n}>")
            elif bullet or ordered:
                if not in_list:
                    out.append("<ul>")
                    in_list = True
                out.append("<li>" + html.escape((bullet or ordered).group(1)) + "</li>")
        else:
            para.append(line)
    flush()
    if in_list:
        out.append("</ul>")
    if in_code:
        out.append("</code></pre>")
    rendered = "<!doctype html><html><head><meta charset=\"utf-8\"><title>" + "Документ" + "</title></head><body>" + "\n".join(out) + "</body></html>"
    for token, fragment in float_blocks.items(): rendered = rendered.replace("<p>" + token + "</p>", fragment)
    return rendered


class _HTMLBlocks(HTMLParser):
    """Extract common semantic blocks for a lightweight, editable DOCX export."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.kind, self.text, self.rows, self.row, self.cell = [], "p", [], [], None, None
        self.skip = 0
        self.base_href = ""
        self.in_style = False
        self.style_text = []
        self.rules = []
        self.ancestors = []

    def close(self):
        super().close()
        if not self.skip and not self.in_style and self.cell is None:
            self._flush()

    @staticmethod
    def _declarations(value):
        result = {}
        for item in str(value or "").split(";"):
            if ":" not in item: continue
            key, val = item.split(":", 1)
            result[key.strip().lower()] = val.strip()
        return result

    @staticmethod
    def _simple_selector_matches(selector, tag, attrs):
        selector = re.sub(r":(?:hover|active|focus|visited|first-child|last-child|nth-child\([^)]*\))", "", selector.strip().lower())
        if not selector or any(ch in selector for ch in ">+~*[]:"): return False
        match = re.fullmatch(r"([a-z][a-z0-9-]*)?((?:\.[a-z0-9_-]+|#[a-z0-9_-]+)*)", selector)
        if not match: return False
        wanted_tag, suffix = match.groups()
        if wanted_tag and wanted_tag != tag.lower(): return False
        classes = set(str(attrs.get("class") or "").lower().split())
        ids = {str(attrs.get("id") or "").lower()}
        return all((value[1:] in classes if value.startswith(".") else value[1:] in ids)
                   for value in re.findall(r"[.#][a-z0-9_-]+", suffix))

    def _parse_style_rules(self):
        css = re.sub(r"/\*.*?\*/", "", "".join(self.style_text), flags=re.S)
        for selector_group, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            declarations = self._declarations(body)
            if not declarations: continue
            for selector in selector_group.split(","):
                parts = [part for part in selector.strip().split() if not part.startswith("@")]
                if not parts: continue
                specificity = sum(100 if part.count("#") else 10 * part.count(".") + (1 if re.match(r"^[a-z]", part) else 0) for part in parts)
                self.rules.append((specificity, len(self.rules), parts, declarations))

    def _computed_style(self, tag, attrs):
        chain = self.ancestors + [{"tag": tag, **attrs}]
        result, priority = {}, {}
        for specificity, order, parts, declarations in self.rules:
            if not self._simple_selector_matches(parts[-1], tag, attrs): continue
            cursor = len(chain) - 2
            matched = True
            for part in reversed(parts[:-1]):
                while cursor >= 0 and not self._simple_selector_matches(part, chain[cursor]["tag"], chain[cursor]): cursor -= 1
                if cursor < 0: matched = False; break
                cursor -= 1
            if matched:
                for key, val in declarations.items():
                    candidate = (specificity, order)
                    if candidate >= priority.get(key, (-1, -1)):
                        result[key], priority[key] = val, candidate
        result.update(self._declarations(attrs.get("style")))
        return result

    def _flush(self):
        value = " ".join("".join(self.text).split())
        if value: self.blocks.append((self.kind, value))
        self.text = []; self.kind = "p"

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "base" and attrs.get("href"):
            self.base_href = attrs["href"]
            return
        if tag == "style": self.in_style = True; self.style_text = []; return
        if tag in {"script", "noscript", "iframe", "svg"}: self.skip += 1; return
        if self.skip: return
        style = self._computed_style(tag, attrs)
        if tag == "table": self._flush(); self.rows = []
        elif tag == "img" and attrs.get("src"):
            self._flush()
            source = urllib.parse.urljoin(self.base_href, attrs["src"]) if self.base_href else attrs["src"]
            float_side = str(style.get("float") or "").lower()
            if float_side not in {"left", "right"}:
                for ancestor in reversed(self.ancestors):
                    inherited_side = str((ancestor.get("_computed") or {}).get("float") or "").lower()
                    if inherited_side in {"left", "right"}:
                        float_side = inherited_side
                        break
            if float_side not in {"left", "right"}:
                align = str(attrs.get("align") or "").lower()
                float_side = align if align in {"left", "right"} else ""
            image = {"src": source, "alt": attrs.get("alt") or "", "float": float_side,
                     "width": style.get("width") or attrs.get("width") or "",
                     "margin": style.get("margin") or "",
                     "margin-left": style.get("margin-left") or "", "margin-right": style.get("margin-right") or ""}
            self.blocks.append(("float_image" if float_side else "image", image))
        elif tag == "tr": self.row = []
        elif tag in {"td", "th"}: self.cell = []
        elif tag in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote"}:
            self._flush(); self.kind = "li" if tag == "li" else tag
        elif tag == "br": self.text.append("\n")
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.ancestors.append({"tag": tag, **attrs, "_computed": style})

    def handle_data(self, data):
        if self.in_style:
            if sum(map(len, self.style_text)) < 512 * 1024: self.style_text.append(data)
            return
        if self.skip: return
        if self.cell is not None: self.cell.append(data)
        else: self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "style": self._parse_style_rules(); self.in_style = False; return
        if tag in {"script", "noscript", "iframe", "svg"}: self.skip = max(0, self.skip - 1); return
        if self.skip: return
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split())); self.cell = None
        elif tag == "tr" and self.row is not None: self.rows.append(self.row); self.row = None
        elif tag == "table":
            self._flush()
            if self.rows: self.blocks.append(("table", self.rows))
        elif tag in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote"}: self._flush()
        for index in range(len(self.ancestors) - 1, -1, -1):
            if self.ancestors[index]["tag"] == tag:
                del self.ancestors[index:]
                break


def _parse_markdown_float_blocks(value: str):
    parser = _HTMLBlocks(); parser.feed(value); parser.close()
    return parser.blocks if any(kind == "float_image" for kind, _data in parser.blocks) else []


def _render_markdown_float_block(blocks):
    output = ['<div style="display:flow-root">']
    for kind, data in blocks:
        if kind == "float_image":
            side = data.get("float") if data.get("float") in {"left", "right"} else "left"
            width = str(data.get("width") or "")
            if not re.fullmatch(r"(?:\d{1,3}(?:\.\d{1,2})?)(?:px|%|rem|em|vw|cm|mm|in)", width, re.I): width = "40%"
            src = str(data.get("src") or "")
            if urllib.parse.urlsplit(src).scheme.lower() not in {"", "http", "https", "data"}: continue
            output.append(f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(str(data.get("alt") or "Изображение"), quote=True)}" style="float:{side};width:{width};max-width:45%;margin:0 14px 10px 0">')
        elif kind in {"p", "li", "blockquote"}:
            text = html.escape(str(data))
            output.append("<p>" + ("• " if kind == "li" else "") + text + "</p>")
    output.append("</div>")
    return "\n".join(output)


def _extract_markdown_float_html(value: str):
    replacements = {}
    pattern = re.compile(r'<div\s+style=["\']display:flow-root["\']\s*>.*?</div\s*>', re.I | re.S)
    def replace(match):
        blocks = _parse_markdown_float_blocks(match.group(0))
        if not blocks: return match.group(0)
        token = f"DEWIAR_FLOAT_BLOCK_{len(replacements)}_TOKEN"
        replacements[token] = _render_markdown_float_block(blocks)
        return token
    return pattern.sub(replace, value), replacements


def _markdown_blocks(value: str) -> list[tuple[str, object]]:
    blocks = []
    lines = value.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1; continue
        if lines[i].strip().startswith('<div style="display:flow-root">'):
            end = i
            while end < len(lines) and "</div>" not in lines[end].lower(): end += 1
            if end < len(lines):
                parsed = _parse_markdown_float_blocks("\n".join(lines[i:end + 1]))
                if parsed:
                    blocks.extend(parsed); i = end + 1; continue
        if i + 1 < len(lines):
            table_rows = _markdown_table_rows(lines[i:])
            if table_rows:
                blocks.append(("table", table_rows))
                i += len(table_rows) + 1
                continue
        j = i + 1
        while j < len(lines) and lines[j].strip(): j += 1
        para = "\n".join(lines[i:j])
        para_lines = para.splitlines()
        heading = re.match(r"^\s*(#{1,6})\s+(.*)", para_lines[0])
        if heading: blocks.append(("h" + str(len(heading.group(1))), heading.group(2).strip())); i = j; continue
        if all(re.match(r"\s*[-*+]\s+", line) for line in para_lines if line.strip()):
            blocks.extend(("li", re.sub(r"^\s*[-*+]\s+", "", line).strip()) for line in para_lines if line.strip()); i = j; continue
        if all(re.match(r"\s*\d+[.)]\s+", line) for line in para_lines if line.strip()):
            blocks.extend(("li", re.sub(r"^\s*\d+[.)]\s+", "", line).strip()) for line in para_lines if line.strip()); i = j; continue
        blocks.append(("p", " ".join(line.strip() for line in para_lines)))
        i = j
    return blocks


def _split_markdown_row(line: str) -> list[str] | None:
    line = line.strip()
    if "|" not in line:
        return None
    if line.startswith("|"): line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"): line = line[:-1]
    cells, current, escaped = [], [], False
    for char in line:
        if char == "|" and not escaped:
            cells.append("".join(current).strip()); current = []
        else:
            current.append(char)
        escaped = char == "\\" and not escaped
        if char != "\\": escaped = False
    cells.append("".join(current).strip())
    return [cell.replace("\\|", "|").replace("\\\\", "\\") for cell in cells]


def _is_markdown_separator(row: list[str] | None) -> bool:
    return bool(row) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in row)


def _markdown_table_rows(lines: list[str]) -> list[list[str]] | None:
    if len(lines) < 2: return None
    header = _split_markdown_row(lines[0])
    sep = _split_markdown_row(lines[1])
    if not header or not _is_markdown_separator(sep) or len(header) != len(sep): return None
    rows = [header]
    i = 2
    while i < len(lines) and lines[i].strip():
        row = _split_markdown_row(lines[i])
        if row is None: break
        rows.append(row + [""] * max(0, len(header) - len(row)))
        i += 1
    return rows


def _write_docx(blocks: list[tuple[str, object]], dest: Path) -> None:
    def esc(value):
        # Office XML 1.0 forbids control characters that may occur in scraped pages.
        text = str(value)
        text = "".join(ch for ch in text if ch in "\t\n\r" or (0x20 <= ord(ch) <= 0xD7FF) or
                       (0xE000 <= ord(ch) <= 0xFFFD) or (0x10000 <= ord(ch) <= 0x10FFFF))
        return html.escape(text, quote=False)
    body = []
    images = []
    for kind, value in blocks:
        if kind in {"image", "float_image"}:
            image_id = len(images) + 1
            name = str(value.get("name") or f"image{image_id}.png")
            ext = Path(name).suffix.lstrip(".").lower() or "png"
            rel_id = f"rId{image_id + 1}"
            cx, cy = int(value.get("cx") or 0), int(value.get("cy") or 0)
            alt = html.escape(esc(value.get("alt") or "Изображение"), quote=True)
            extent = f'<wp:extent cx="{cx}" cy="{cy}"/>'
            properties = (f'<wp:docPr id="{image_id}" name="{html.escape(name, quote=True)}" descr="{alt}"/>'
                          '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>')
            picture = (f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="{image_id}" name="{html.escape(name, quote=True)}"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>')
            if kind == "float_image":
                side = "right" if value.get("float") == "right" else "left"
                drawing = (f'<wp:anchor distT="0" distB="0" distL="{int(value.get("distL", 137160))}" distR="{int(value.get("distR", 137160))}" simplePos="0" relativeHeight="{251658240 + image_id}" behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">'
                           '<wp:simplePos x="0" y="0"/>'
                           f'<wp:positionH relativeFrom="margin"><wp:align>{side}</wp:align></wp:positionH>'
                           '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>'
                           + extent + '<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapSquare wrapText="bothSides"/>'
                           + properties + picture + '</wp:anchor>')
            else:
                drawing = f'<wp:inline distT="0" distB="0" distL="0" distR="0">{extent}{properties}{picture}</wp:inline>'
            body.append(f'<w:p><w:r><w:drawing>{drawing}</w:drawing></w:r></w:p>')
            images.append((rel_id, name, ext, value["data"]))
            continue
        if kind == "table":
            rows = value
            row_xml = []
            width = max((len(row) for row in rows), default=1)
            for row in rows:
                cells = "".join("<w:tc><w:tcPr><w:tcW w:w=\"2400\" w:type=\"dxa\"/></w:tcPr><w:p><w:r><w:t xml:space=\"preserve\">" + esc(cell) + "</w:t></w:r></w:p></w:tc>" for cell in row + [""] * (width-len(row)))
                row_xml.append("<w:tr>" + cells + "</w:tr>")
            body.append("<w:tbl><w:tblPr><w:tblBorders>" + "".join(f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="B8C0CC"/>' for edge in ("top", "left", "bottom", "right", "insideH", "insideV")) + "</w:tblBorders></w:tblPr>" + "".join(row_xml) + "</w:tbl>")
            continue
        text = str(value)
        if kind == "li": text = "• " + text
        ppr = f'<w:pPr><w:pStyle w:val="Heading{kind[1]}"/></w:pPr>' if kind.startswith("h") and kind[1:].isdigit() else ""
        text = text.replace("\n", " ")
        body.append("<w:p>" + ppr + "<w:r><w:t xml:space=\"preserve\">" + esc(text) + "</w:t></w:r></w:p>")
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                '<w:body>' + "".join(body) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="708" w:footer="708" w:gutter="0"/>'
                '</w:sectPr></w:body></w:document>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             + "".join(f'<Default Extension="{ext}" ContentType="{"image/jpeg" if ext in {"jpg", "jpeg"} else "image/png"}"/>' for ext in sorted({row[2] for row in images})) +
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    document_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
                     "".join(f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{name}"/>' for rel_id,name,_ext_,_data in images) +
                     '</Relationships>')
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", types)
        zf.writestr("_rels/.rels", rels)
        if images: zf.writestr("word/_rels/document.xml.rels", document_rels)
        zf.writestr("word/document.xml", document)
        for _rel_id, name, _ext_, data in images: zf.writestr("word/media/" + name, data)


def _html_docx_blocks(src: Path, blocks: list[tuple[str, object]], cancel=None):
    """Embed safe raster images referenced by HTML into an OOXML Word document."""
    import base64
    import hashlib
    from PIL import Image, ImageOps

    parser = _HTMLBlocks(); parser.feed(_read_text(src)); parser.close()
    base = parser.base_href
    prepared = []
    image_count, total_bytes = 0, 0
    image_refs = sum(1 for kind, _value in blocks if kind in {"image", "float_image"})
    failed, limit_reached = 0, False
    def gap_emu(value, default=137160):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)(px|pt|in|cm|mm)\s*", str(value or ""), re.I)
        if not match: return default
        amount, unit = float(match.group(1)), match.group(2).lower()
        result = amount * {"px":9525, "pt":12700, "in":914400, "cm":360000, "mm":36000}[unit]
        return int(max(0, min(457200, result)))
    for kind, value in blocks:
        if kind not in {"image", "float_image"}:
            prepared.append((kind, value)); continue
        if cancel is not None and cancel.is_set(): raise ConversionError("Конвертация отменена")
        if image_count >= 12 or total_bytes >= 30 * 1024 * 1024:
            limit_reached = True; continue
        raw_url = str(value.get("src") or "").strip()
        if not raw_url: continue
        data = b""
        try:
            if raw_url.startswith("data:image/"):
                header, encoded = raw_url.split(",", 1)
                if ";base64" not in header or len(encoded) > 11_000_000: raise ValueError("слишком большой data URL")
                data = base64.b64decode(encoded, validate=True)
            else:
                resolved = urllib.parse.urljoin(base, raw_url) if base else raw_url
                parts = urllib.parse.urlsplit(resolved)
                if parts.scheme in {"http", "https"}:
                    if parts.path.lower().endswith(".svg"): continue
                    resolved = urllib.parse.urlunsplit((parts.scheme, parts.netloc,
                        urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/%:@!$&'()*+,;=-._~"),
                        urllib.parse.quote(urllib.parse.unquote(parts.query), safe="!$&'()*+,;=:@/?%"), ""))
                    current = _validate_public_web_url(resolved)
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {"redirect_request": lambda self, req, fp, code, msg, headers, newurl: None})())
                    for _ in range(4):
                        try:
                            response = opener.open(urllib.request.Request(current, headers={"User-Agent":"Dewiar/1.0 (document conversion)","Accept":"image/png,image/jpeg,image/webp,image/gif"}), timeout=12)
                        except urllib.error.HTTPError as exc:
                            if exc.code in {301,302,303,307,308} and exc.headers.get("Location"):
                                current = _validate_public_web_url(urllib.parse.urljoin(current, exc.headers["Location"])); continue
                            raise ValueError(f"HTTP {exc.code}")
                        with response:
                            if not response.headers.get_content_type().startswith("image/"): raise ValueError("ответ не изображение")
                            data = response.read(8 * 1024 * 1024 + 1)
                        break
                elif not parts.scheme:
                    local = (src.parent / urllib.parse.unquote(parts.path)).resolve()
                    if src.parent.resolve() not in local.parents or not local.is_file(): raise ValueError("локальный путь изображения недоступен")
                    if local.stat().st_size > 8 * 1024 * 1024: raise ValueError("изображение больше 8 МБ")
                    data = local.read_bytes()
                else: raise ValueError("поддерживаются только публичные HTTPS/HTTP, data URL и локальные изображения рядом с HTML")
            if not data or len(data) > 8 * 1024 * 1024: raise ValueError("изображение больше 8 МБ или пустое")
            with Image.open(io.BytesIO(data)) as original:
                image = ImageOps.exif_transpose(original.copy())
                if image.width < 16 or image.height < 16: continue
                if image_count >= 12 or total_bytes + len(data) > 30 * 1024 * 1024:
                    limit_reached = True; continue
                if image.format == "JPEG" and image.mode in {"RGB", "L"}:
                    ext, payload = "jpg", data
                else:
                    if image.mode not in {"RGB", "RGBA"}: image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                    stream = io.BytesIO(); image.save(stream, format="PNG", optimize=True)
                    ext, payload = "png", stream.getvalue()
                if total_bytes + len(payload) > 30 * 1024 * 1024: raise ValueError("превышен общий лимит изображений")
                max_width_emu = (2.8 if kind == "float_image" else 6.4) * 914400
                raw_width = str(value.get("width") or "").strip()
                requested_width = 0
                width_match = re.fullmatch(r"(\d+(?:\.\d+)?)(px|pt|in|cm|mm|%)", raw_width, re.I)
                if width_match:
                    amount, unit = float(width_match.group(1)), width_match.group(2).lower()
                    requested_width = amount * {"px":9525, "pt":12700, "in":914400, "cm":360000, "mm":36000}.get(unit, max_width_emu / 100)
                    if unit == "%": requested_width = max_width_emu * min(amount, 100) / 100
                desired_width_emu = min(max_width_emu, requested_width) if requested_width else max_width_emu
                factor = min(1.0, desired_width_emu / (image.width * 9525), 6.4 * 914400 / (image.height * 9525))
                # Each occurrence needs its own relationship and anchor, even
                # when the source URL is reused on both sides of a product card.
                name = hashlib.sha256(payload).hexdigest()[:12] + f"-{image_count + 1}." + ext
                margin_parts = str(value.get("margin") or "").split()
                if len(margin_parts) == 1: margin_left = margin_right = margin_parts[0]
                elif len(margin_parts) == 2: margin_left = margin_right = margin_parts[1]
                elif len(margin_parts) == 3: margin_right = margin_parts[1]; margin_left = margin_parts[1]
                elif len(margin_parts) >= 4: margin_right, margin_left = margin_parts[1], margin_parts[3]
                else: margin_left = margin_right = ""
                margin_left = value.get("margin-left") or margin_left
                margin_right = value.get("margin-right") or margin_right
                prepared.append((kind, {"name":name, "data":payload, "alt":str(value.get("alt") or "")[:300],
                                             "float":value.get("float") if kind == "float_image" else "",
                                             "distL":gap_emu(margin_left), "distR":gap_emu(margin_right),
                                             "cx":int(image.width*9525*factor), "cy":int(image.height*9525*factor)}))
                image_count += 1; total_bytes += len(payload)
        except Exception as exc:
            failed += 1
    warnings = []
    if failed: warnings.append(f"Не удалось встроить {failed} из {image_refs} изображений; внешние SVG, трекеры и недоступные картинки пропущены.")
    if limit_reached: warnings.append("Достигнут лимит встраивания: до 12 изображений и 30 МБ суммарно.")
    return prepared, warnings


def _docx_blocks(path: Path) -> list[tuple[str, object]]:
    try:
        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ConversionError("Не удалось прочитать DOCX: файл повреждён или имеет неверный формат") from exc
    body = root.find(".//w:body", XML_NS)
    if body is None:
        return ""
    blocks = []
    for child in body:
        if child.tag.endswith("}p"):
            text = "".join(node.text or "" for node in child.iter() if node.tag.endswith("}t"))
            ppr = child.find("w:pPr/w:pStyle", XML_NS)
            style = ppr.attrib.get(f"{{{XML_NS['w']}}}val", "") if ppr is not None else ""
            if text.strip():
                if style.lower().startswith("heading"):
                    m = re.search(r"(\d+)$", style)
                    blocks.append(("h" + str(min(6, int(m.group(1)) if m else 1)), text.strip()))
                else:
                    blocks.append(("p", text.strip()))
        elif child.tag.endswith("}tbl"):
            rows = []
            for row in child.findall(".//w:tr", XML_NS):
                cells = [" ".join("".join(node.text or "" for node in p.iter() if node.tag.endswith("}t"))
                                   for p in cell.findall(".//w:p", XML_NS)).strip()
                         for cell in row.findall("w:tc", XML_NS)]
                rows.append(cells)
            if rows: blocks.append(("table", rows))
    return blocks


def _docx_text(path: Path, markdown=False) -> str:
    blocks = _docx_blocks(path)
    rendered = []
    for kind, value in blocks:
        if kind == "table":
            rows = value
            if markdown:
                rendered.append(_rows_markdown(rows))
            else:
                rendered.append("\n".join("\t".join(row) for row in rows))
        elif kind.startswith("h") and markdown:
            rendered.append("#" * int(kind[1:]) + " " + str(value))
        else:
            rendered.append(str(value))
    return "\n\n".join(rendered)


def _docx_html(path: Path, title: str) -> str:
    out = []
    for kind, value in _docx_blocks(path):
        if kind == "table":
            out.append(_rows_html(value, title).split("<body>", 1)[1].split("</body>", 1)[0])
        elif kind.startswith("h"):
            out.append(f"<{kind}>{html.escape(str(value))}</{kind}>")
        else:
            out.append("<p>" + html.escape(str(value)) + "</p>")
    return "<!doctype html><html><head><meta charset=\"utf-8\"><title>" + html.escape(title) + "</title></head><body>" + "".join(out) + "</body></html>"


def _write_pdf_text(src: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(src), strict=False)
        text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    except Exception as exc:
        raise ConversionError(f"Не удалось извлечь текст PDF: {exc}") from exc
    if not text:
        raise ConversionError("В PDF нет текстового слоя. Для скана нужен отдельный OCR")
    return text


def _csv_rows(path: Path) -> tuple[list[list[str]], str]:
    text = _read_text(path)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if _ext(path) == "tsv" else csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)], "\t" if _ext(path) == "tsv" else ","


def _col_name(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _build_xlsx(rows: list[list[str]], path: Path) -> None:
    def esc(value):
        return html.escape(str(value), quote=False)
    cells = []
    for ri, row in enumerate(rows, 1):
        parts = []
        for ci, value in enumerate(row, 1):
            ref = f"{_col_name(ci)}{ri}"
            # Preserve CSV content exactly as strings (not formulas), including
            # leading zeroes and values beginning with '='.
            parts.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{esc(value)}</t></is></c>')
        cells.append(f'<row r="{ri}">' + "".join(parts) + "</row>")
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
             '<sheetData>' + "".join(cells) + '</sheetData></worksheet>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Данные" sheetId="1" r:id="rId1"/></sheets></workbook>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


def _xlsx_sheet(path: Path) -> list[list[str]]:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            relroot = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            relmap = {r.attrib["Id"]: r.attrib["Target"] for r in relroot.findall("rel:Relationship", XML_NS)}
            sheets = wb.findall("s:sheets/s:sheet", XML_NS)
            if not sheets:
                raise ConversionError("В XLSX нет листов")
            active = 0
            view = wb.find("s:bookViews/s:workbookView", XML_NS)
            if view is not None:
                active = min(int(view.attrib.get("activeTab", "0") or 0), len(sheets) - 1)
            sheet = sheets[active]
            target = relmap.get(sheet.attrib.get(f"{{{XML_NS['r']}}}id"), "worksheets/sheet1.xml").lstrip("/")
            sheet_path = posixpath.normpath(target if target.startswith("xl/") else "xl/" + target)
            if sheet_path not in names:
                raise ConversionError("В книге отсутствует XML выбранного листа")
            shared = []
            if "xl/sharedStrings.xml" in names:
                sr = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                shared = ["".join(x.text or "" for x in item.findall(".//s:t", XML_NS)) for item in sr.findall("s:si", XML_NS)]
            root = ET.fromstring(zf.read(sheet_path))
    except ConversionError:
        raise
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError, ValueError) as exc:
        raise ConversionError("Не удалось прочитать XLSX: файл повреждён или имеет неподдерживаемую структуру") from exc
    rows: dict[int, dict[int, str]] = {}
    for cell in root.findall(".//s:sheetData/s:row/s:c", XML_NS):
        ref = cell.attrib.get("r", "")
        m = re.match(r"([A-Z]+)(\d+)", ref)
        if not m:
            continue
        col = 0
        for char in m.group(1):
            col = col * 26 + ord(char) - 64
        row = int(m.group(2))
        typ = cell.attrib.get("t", "")
        val = cell.find("s:v", XML_NS)
        if typ == "inlineStr":
            value = "".join(x.text or "" for x in cell.findall(".//s:t", XML_NS))
        elif typ == "s" and val is not None:
            try:
                value = shared[int(val.text or "0")]
            except (ValueError, IndexError):
                value = ""
        elif typ == "b" and val is not None:
            value = "TRUE" if val.text == "1" else "FALSE"
        else:
            value = val.text if val is not None and val.text is not None else ""
        rows.setdefault(row, {})[col] = value
    if not rows:
        return []
    max_row, max_col = max(rows), max((max(r) for r in rows.values()), default=0)
    return [[rows.get(r, {}).get(c, "") for c in range(1, max_col + 1)] for r in range(1, max_row + 1)]


def _soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def _office_convert(src: Path, target: str, outdir: Path, cancel=None) -> Path:
    executable = _soffice()
    if not executable:
        raise ConversionError("Для этого офисного формата нужен LibreOffice (программа soffice). Установите его и повторите.")
    target = "html" if target == "htm" else target
    profile = tempfile.mkdtemp(prefix="dewiar-lo-")
    temp_out = Path(tempfile.mkdtemp(prefix=".lo-out-", dir=outdir))
    try:
        command = [executable, "--headless", "--nologo", "--nodefault", "--nofirststartwizard", "--norestore",
                   f"-env:UserInstallation={Path(profile).as_uri()}", "--convert-to", target,
                   "--outdir", str(temp_out), str(src)]
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, shell=False)
        deadline = time.monotonic() + 180
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise ConversionError("Конвертация отменена")
            if time.monotonic() >= deadline:
                proc.kill()
                raise ConversionError("LibreOffice не завершил конвертацию за 3 минуты")
            time.sleep(.1)
        stdout, stderr = proc.communicate()
        dest = temp_out / (src.stem + "." + target)
        if proc.returncode or not dest.is_file() or dest.stat().st_size == 0:
            detail = (stderr or stdout or "неизвестная ошибка").strip().splitlines()[-1][:300]
            raise ConversionError(f"LibreOffice не создал {dest.name}: {detail}")
        return dest
    except OSError as exc:
        shutil.rmtree(temp_out, ignore_errors=True)
        raise ConversionError(f"Не удалось запустить LibreOffice: {exc}") from exc
    except Exception:
        shutil.rmtree(temp_out, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def _ffmpeg() -> str:
    try:
        from eyes import ffmpeg_path
        path = ffmpeg_path()
    except Exception:
        path = shutil.which("ffmpeg")
    if not path:
        raise ConversionError("Для аудиоконвертации нужен встроенный ffmpeg, но он недоступен в этой сборке")
    return path


def _move_office_result(generated: Path, dest: Path) -> Path:
    generated.replace(dest)
    shutil.rmtree(generated.parent, ignore_errors=True)
    return dest


def _image_convert(src: Path, dest: Path, target: str) -> Path:
    try:
        from PIL import Image, ImageOps, ImageSequence
        with Image.open(src) as im:
            fmt = IMAGE_FORMAT[target]
            frames = []
            durations = []
            animated = bool(getattr(im, "is_animated", False))
            sequence = ImageSequence.Iterator(im) if animated else [im]
            for frame in sequence:
                converted = ImageOps.exif_transpose(frame.copy())
                if fmt == "JPEG":
                    if converted.mode in ("RGBA", "LA") or "transparency" in converted.info:
                        rgba = converted.convert("RGBA")
                        bg = Image.new("RGB", rgba.size, "white")
                        bg.paste(rgba, mask=rgba.getchannel("A"))
                        converted = bg
                    else:
                        converted = converted.convert("RGB")
                elif fmt == "GIF":
                    converted = converted.convert("P", palette=Image.Palette.ADAPTIVE)
                elif fmt == "BMP":
                    converted = converted.convert("RGB")
                elif fmt == "PNG" and converted.mode == "P":
                    converted = converted.convert("RGBA") if "transparency" in converted.info else converted.convert("RGB")
                frames.append(converted)
                durations.append(frame.info.get("duration", im.info.get("duration", 100)))
            if not frames:
                raise ConversionError("В изображении нет кадров")
            save_options = {}
            if fmt == "GIF" and len(frames) > 1:
                save_options.update(save_all=True, append_images=frames[1:], duration=durations, loop=im.info.get("loop", 0), disposal=2)
            frames[0].save(dest, format=fmt, **save_options)
        return dest
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Не удалось преобразовать изображение: {exc}") from exc


def _image_to_svg(src: Path, dest: Path) -> Path:
    """Wrap a normalized PNG in SVG while preserving the original raster look."""
    import base64
    from PIL import Image, ImageOps
    try:
        with Image.open(src) as original:
            image = ImageOps.exif_transpose(original.copy())
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            stream = io.BytesIO()
            image.save(stream, format="PNG", optimize=True)
            png_bytes = stream.getvalue()
            if len(png_bytes) > 32 * 1024 * 1024:
                raise ConversionError("PNG внутри SVG превышает безопасный предел 32 МБ")
            encoded = base64.b64encode(png_bytes).decode("ascii")
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" height="{image.height}" '
                   f'viewBox="0 0 {image.width} {image.height}"><image width="{image.width}" height="{image.height}" '
                   f'href="data:image/png;base64,{encoded}"/></svg>')
        dest.write_text(svg, encoding="utf-8")
        return dest
    except Exception as exc:
        raise ConversionError(f"Не удалось преобразовать изображение в SVG: {exc}") from exc


def _write_text_format(src: Path, target: str, dest: Path) -> Path:
    ext = _ext(src)
    if ext in {"docx"}:
        text = _docx_text(src, markdown=target == "md")
    elif ext in {"html", "htm"}:
        raw = _read_text(src)
        text = _html_to_markdown(raw) if target == "md" else _html_to_text(raw)
    elif ext == "pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(src), strict=False)
            text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
        except Exception as exc:
            raise ConversionError(f"Не удалось извлечь текст PDF: {exc}") from exc
        if not text:
            raise ConversionError("В PDF нет текстового слоя. Для скана нужен отдельный OCR")
    elif ext in {"csv", "tsv"}:
        rows, _ = _csv_rows(src)
        text = "\n".join("\t".join(row) for row in rows) if target == "txt" else _rows_markdown(rows)
    elif ext == "xlsx":
        rows = _xlsx_sheet(src)
        text = "\n".join("\t".join(row) for row in rows) if target == "txt" else _rows_markdown(rows)
    else:
        text = _read_text(src)
        if target == "md" and ext not in {"md", "markdown"}:
            text = "\n".join(line for line in text.splitlines())
    if target == "html":
        if ext in {"html", "htm"}:
            dest.write_text(_read_text(src), encoding="utf-8")
        elif ext == "docx":
            dest.write_text(_docx_html(src, src.stem), encoding="utf-8")
        elif ext == "pdf":
            dest.write_text("<!doctype html><html><head><meta charset=\"utf-8\"><title>" + html.escape(src.stem) +
                            "</title></head><body>" + "".join("<p>" + html.escape(line) + "</p>" for line in text.splitlines() if line.strip()) + "</body></html>", encoding="utf-8")
        elif ext in {"md", "markdown"}:
            dest.write_text(_markdown_html(text), encoding="utf-8")
        elif ext in {"csv", "tsv"}:
            dest.write_text(_rows_html(_csv_rows(src)[0], src.stem), encoding="utf-8")
        elif ext == "xlsx":
            dest.write_text(_rows_html(_xlsx_sheet(src), src.stem), encoding="utf-8")
        else:
            dest.write_text("<!doctype html><html><head><meta charset=\"utf-8\"><title>" + html.escape(src.stem) +
                            "</title></head><body><pre>" + html.escape(text) + "</pre></body></html>", encoding="utf-8")
    else:
        dest.write_text(text.rstrip() + "\n", encoding="utf-8")
    if ext in {"html", "htm"} and target == "md":
        # Keep local page images beside the Markdown document so relative
        # references still work after conversion moves the output directory.
        parser = _HTMLBlocks()
        parser.feed(_read_text(src)); parser.close()
        for kind, block in parser.blocks:
            if kind not in {"image", "float_image"}: continue
            source = str(block.get("src") or "")
            parsed = urllib.parse.urlsplit(source)
            if parsed.scheme or parsed.netloc or not parsed.path: continue
            candidate = (src.parent / urllib.parse.unquote(parsed.path)).resolve()
            try: candidate.relative_to(src.parent.resolve())
            except ValueError: continue
            if not candidate.is_file(): continue
            name = candidate.name
            target_file = dest.parent / name
            if target_file.resolve() == candidate: continue
            if target_file.exists() and target_file.read_bytes() != candidate.read_bytes():
                name = f"{candidate.stem}-{hashlib.sha256(candidate.read_bytes()).hexdigest()[:8]}{candidate.suffix}"
                target_file = dest.parent / name
            if not target_file.exists(): shutil.copy2(candidate, target_file)
            text = text.replace(f"]({source})", f"]({urllib.parse.quote(name)})")
        dest.write_text(text.rstrip() + "\n", encoding="utf-8")
    return dest


def _text_to_table(src: Path) -> list[list[str]]:
    if _ext(src) in {"csv", "tsv"}:
        return _csv_rows(src)[0]
    if _ext(src) == "xlsx":
        return _xlsx_sheet(src)
    if _ext(src) in {"html", "htm"}:
        raw = _read_text(src)
        table = _html_table_rows(raw)
        if table:
            return table
        text = _html_to_text(raw)
    elif _ext(src) == "docx":
        blocks = _docx_blocks(src)
        tables = [rows for kind, rows in blocks if kind == "table"]
        if tables:
            result = []
            for index, rows in enumerate(tables):
                if index: result.append([])
                result.extend(rows)
            return result
        text = "\n".join(str(value) for _kind, value in blocks)
    elif _ext(src) in {"md", "markdown"}:
        lines = _read_text(src).replace("\r\n", "\n").replace("\r", "\n").splitlines()
        tables = []
        i = 0
        while i < len(lines):
            parsed = _markdown_table_rows(lines[i:])
            if parsed:
                tables.append(parsed); i += len(parsed) + 1
            else: i += 1
        if tables:
            result = []
            for index, rows in enumerate(tables):
                if index: result.append([])
                result.extend(rows)
            return result
        text = _read_text(src)
    else:
        text = _read_text(src)
    return [[line] for line in text.splitlines()]


def _rows_html(rows: list[list[str]], title: str) -> str:
    width = max((len(row) for row in rows), default=0)
    head, body = (rows[:1], rows[1:]) if rows else ([], [])
    table = "<table><thead>" + "".join("<tr>" + "".join("<th>" + html.escape(c) + "</th>" for c in (row + [""] * (width-len(row)))) + "</tr>" for row in head) + "</thead><tbody>"
    table += "".join("<tr>" + "".join("<td>" + html.escape(c) + "</td>" for c in (row + [""] * (width-len(row)))) + "</tr>" for row in body)
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>" + html.escape(title) +
            "</title><style>body{font:11pt sans-serif}table{border-collapse:collapse}td,th{border:1px solid #888;padding:4px}</style></head><body>" + table + "</body></html>")


def _rows_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(map(len, rows))
    normal = [[cell.replace("|", "\\|").replace("\n", " ") for cell in row + [""] * (width-len(row))] for row in rows]
    return "| " + " | ".join(normal[0]) + " |\n| " + " | ".join(["---"] * width) + " |" + "\n" + "\n".join("| " + " | ".join(row) + " |" for row in normal[1:])


def _csv_write(rows: list[list[str]], dest: Path, sep: str) -> Path:
    with dest.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=sep, lineterminator="\r\n")
        writer.writerows(rows)
    return dest


def convert(src: str | Path, target: str, output_dir: str | Path, *, cancel=None) -> dict:
    """Convert one local file and return {path, warning, backend}."""
    src = Path(src).resolve()
    outdir = Path(output_dir).resolve()
    if not src.is_file():
        raise ConversionError(f"Исходный файл не найден: {src.name}")
    if src.stat().st_size > 2 * 1024**3:
        raise ConversionError("Файл больше 2 ГБ — безопасный предел конвертации")
    target = normalise_target(target)
    ext = _ext(src)
    if ext == target or {ext, target} <= {"jpg", "jpeg"}:
        raise ConversionError("Файл уже имеет этот формат")
    allowed = targets_for(src)
    if target not in allowed:
        raise ConversionError(f"Преобразование .{ext or '?'} → .{target} не поддерживается")
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / f"{_safe_stem(src)}.{target}"
    if dest.exists():
        n = 2
        while (outdir / f"{_safe_stem(src)} ({n}).{target}").exists():
            n += 1
        dest = outdir / f"{_safe_stem(src)} ({n}).{target}"
    if cancel is not None and cancel.is_set():
        raise ConversionError("Конвертация отменена")
    conversion_warnings = []
    if ext in IMAGE_EXTS and target == "svg":
        _image_to_svg(src, dest)
        backend = "Pillow → SVG"
    elif ext in IMAGE_EXTS and target in IMAGE_FORMAT:
        _image_convert(src, dest, target)
        backend = "Pillow"
    elif ext in IMAGE_EXTS and target == "pdf":
        try:
            from PIL import Image, ImageOps
            with Image.open(src) as image:
                image = ImageOps.exif_transpose(image.copy()).convert("RGB")
                image.save(dest, "PDF", resolution=150)
            backend = "Pillow"
        except Exception as exc:
            raise ConversionError(f"Не удалось создать PDF из изображения: {exc}") from exc
    elif (ext in AUDIO_EXTS or ext in VIDEO_EXTS) and target in MEDIA_OUT:
        ffmpeg = _ffmpeg()
        codec_args = {"mp3": ["-codec:a", "libmp3lame", "-q:a", "2"], "mpeg": ["-codec:a", "libmp3lame", "-q:a", "2"],
                      "wav": ["-codec:a", "pcm_s16le"], "flac": ["-codec:a", "flac"], "ogg": ["-codec:a", "libvorbis"],
                      "opus": ["-codec:a", "libopus"]}
        args = [ffmpeg, "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe", "-i", str(src), "-vn"]
        args += codec_args.get(target, [])
        args.append(str(dest))
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        except OSError as exc:
            raise ConversionError(f"Не удалось запустить ffmpeg: {exc}") from exc
        deadline = time.monotonic() + 300
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                dest.unlink(missing_ok=True)
                raise ConversionError("Конвертация отменена")
            if time.monotonic() >= deadline:
                proc.kill()
                proc.communicate()
                dest.unlink(missing_ok=True)
                raise ConversionError("ffmpeg не завершил преобразование за 5 минут")
            time.sleep(.1)
        _, stderr = proc.communicate()
        if proc.returncode or not dest.is_file() or not dest.stat().st_size:
            dest.unlink(missing_ok=True)
            detail = stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:] or ["неизвестная ошибка"]
            raise ConversionError("ffmpeg: " + detail[0][:300])
        backend = "ffmpeg"
    elif target in {"csv", "tsv"}:
        if ext == "xlsx":
            rows = _xlsx_sheet(src)
        elif ext in {"csv", "tsv"}:
            rows = _csv_rows(src)[0]
        else:
            rows = _text_to_table(src)
        _csv_write(rows, dest, "\t" if target == "tsv" else ",")
        backend = "stdlib"
    elif target == "xlsx":
        rows = _text_to_table(src)
        _build_xlsx(rows, dest)
        backend = "stdlib OOXML"
    elif target in {"txt", "md", "html"} and ext in (TEXT_EXTS | {"docx", "pdf", "xlsx"}):
        _write_text_format(src, target, dest)
        backend = "stdlib" if ext not in {"pdf"} else "pypdf"
    elif target == "docx" and ext in (TEXT_EXTS | {"docx", "pdf", "xlsx"}):
        if ext == "docx":
            raise ConversionError("Файл уже в DOCX")
        if ext == "pdf":
            text = _write_pdf_text(src)
            blocks = [("p", line) for line in text.splitlines() if line.strip()]
        elif ext == "xlsx":
            blocks = [("table", _xlsx_sheet(src))]
        elif ext in {"csv", "tsv"}:
            blocks = [("table", _csv_rows(src)[0])]
        elif ext in {"html", "htm"}:
            parser = _HTMLBlocks(); parser.feed(_read_text(src)); parser.close(); blocks = parser.blocks
            blocks, image_warnings = _html_docx_blocks(src, blocks, cancel)
            conversion_warnings.extend(image_warnings)
        elif ext in {"md", "markdown"}:
            blocks = _markdown_blocks(_read_text(src))
            blocks, image_warnings = _html_docx_blocks(src, blocks, cancel)
            conversion_warnings.extend(image_warnings)
        else:
            blocks = [("p", line) for line in _read_text(src).splitlines() if line.strip()]
        _write_docx(blocks, dest)
        backend = "stdlib OOXML"
    elif target == "docx" and ext in OFFICE_EXTS:
        if ext in {"doc", "odt", "rtf"}:
            text_file = _office_convert(src, "txt", outdir, cancel)
            _write_docx([("p", line) for line in _read_text(text_file).splitlines() if line.strip()], dest)
            text_file.unlink(missing_ok=True)
            shutil.rmtree(text_file.parent, ignore_errors=True)
            backend = "LibreOffice + OOXML"
        else:
            raise ConversionError(f"Офисное преобразование .{ext} → .docx пока не поддерживается")
    elif target == "pdf" and ext in TEXT_EXTS:
        html_temp = src.parent / ("." + dest.stem + ".print.html")
        try:
            if ext in {"csv", "tsv"}:
                content = _rows_html(_csv_rows(src)[0], src.stem)
            elif ext in {"md", "markdown"}:
                content = _markdown_html(_read_text(src))
            elif ext in {"html", "htm"}:
                content = _read_text(src)
            else:
                content = "<!doctype html><meta charset=utf-8><body><pre>" + html.escape(_read_text(src)) + "</pre></body>"
            html_temp.write_text(content, encoding="utf-8")
            if cancel is not None and cancel.is_set():
                raise ConversionError("Преобразование отменено")
            from export import html_to_pdf
            html_to_pdf(html_temp, dest)
        finally:
            html_temp.unlink(missing_ok=True)
        backend = "Chromium"
    elif target in {"pdf", "html", "txt", "docx", "xlsx", "csv"} and ext in (OFFICE_EXTS | {"pdf"}):
        if ext == "pdf" and target == "pdf":
            raise ConversionError("Файл уже PDF")
        if ext == "pdf" and target in {"html", "txt", "docx"}:
            if target == "docx":
                text = _write_pdf_text(src)
                _write_docx([("p", line) for line in text.splitlines() if line.strip()], dest)
                backend = "pypdf + stdlib OOXML"
            else:
                _write_text_format(src, target, dest)
                backend = "pypdf"
            generated = None
        elif ext == "xlsx" and target == "docx":
            _write_docx([("table", _xlsx_sheet(src))], dest)
            backend = "stdlib OOXML"
            generated = None
        else:
            generated = _office_convert(src, target, outdir, cancel)
            backend = "LibreOffice"
        if generated is not None and generated != dest:
            _move_office_result(generated, dest)
    else:
        raise ConversionError(f"Преобразование .{ext or '?'} → .{target} не поддерживается")
    if not dest.is_file() or dest.stat().st_size == 0:
        raise ConversionError("Конвертер не создал непустой результат")
    warning = "; ".join(conversion_warnings)
    if ext == "xlsx" and target in {"csv", "tsv"}:
        warning = "В CSV сохранён только активный лист книги; форматирование и формулы не переносятся."
    if target == "jpg" and ext in IMAGE_EXTS:
        warning = "JPEG не поддерживает прозрачность; прозрачные области заменены белым фоном."
    if ext == "pdf" and target in {"txt", "md"}:
        warning = "Из PDF извлечён только текстовый слой; сканированные страницы требуют OCR."
    return {"path": dest, "backend": backend, "warning": warning}
