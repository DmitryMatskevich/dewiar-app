"""Clean transport escapes in prose before display, history and speech.

Code, inline examples, URLs and Windows paths are literal data. Never use
unicode_escape or JSON-decode an entire answer: that corrupts Cyrillic and code.
"""
import re

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufeff\u200b]")
_ESC = re.compile(r'\\+(?:u([0-9a-fA-F]{4})|([nrtbfv])|(["/]))')
_SURROGATE = re.compile(r"\\+u(d[89ab][0-9a-f]{2})\\+u(d[cdef][0-9a-f]{2})", re.I)
_PATH = re.compile(r"(?:^|[^\w])(?:[A-Za-z]:\\|\\\\[\w.-]+\\)")


def _unescape(match):
    if match[1]:
        code = int(match[1], 16)
        return chr(code) if not 0xD800 <= code <= 0xDFFF else "�"
    return {"n": "\n", "r": "\n", "t": " ", "b": "", "f": " ", "v": " "}.get(match[2], match[3] or "")


class ResponseTextFilter:
    """Incremental, at most one word behind the model; escapes may span chunks."""

    def __init__(self):
        self.pending = ""
        self.fence = ""
        self.inline = 0
        self.beginning = True
        self.structured = False

    def _word(self, word):
        if self.beginning:
            self.beginning = False
            self.structured = word in ("{", "[") or word.startswith(('{"', '[{', '["'))
        if self.structured:
            return word  # a requested JSON document is literal data, like fenced code
        fence = re.match(r"^(`{3,}|~{3,})", word)
        if fence and not self.inline:
            marker = fence[1]
            if not self.fence:
                self.fence = marker
            elif marker[0] == self.fence[0] and len(marker) >= len(self.fence):
                self.fence = ""
            return word
        if self.fence:
            return word
        protected = bool(self.inline)
        for ticks in re.finditer(r"`+", word):
            protected = True
            size = len(ticks[0])
            if not self.inline:
                self.inline = size
            elif self.inline == size:
                self.inline = 0
        if protected or _PATH.search(word) or re.search(r"https?://|www\.", word):
            return word
        # Decode only known escapes, once. Unknown sequences remain meaningful symbols.
        word = word.replace(r"\r\n", "\n")
        word = _SURROGATE.sub(lambda m: chr(0x10000 + ((int(m[1], 16) - 0xD800) << 10) + int(m[2], 16) - 0xDC00), word)
        word = _ESC.sub(_unescape, word)
        word = re.sub(r"\\([*_#>|~!\[\]{}().+-])", r"\1", word)
        return _CONTROLS.sub("", _ANSI.sub("", word))

    def feed(self, text):
        out = []
        for ch in text:
            if ch.isspace():
                if self.pending:
                    out.append(self._word(self.pending))
                    self.pending = ""
                out.append(ch)
            else:
                self.pending += ch
        return "".join(out)

    def flush(self):
        word, self.pending = self.pending, ""
        return self._word(word) if word else ""


def clean_response(text):
    f = ResponseTextFilter()
    return f.feed(text) + f.flush()
