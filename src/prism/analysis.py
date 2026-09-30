import re
import unicodedata
from dataclasses import dataclass

VERSION = "nfkc-casefold-unicode-v1"
TEXT_FIELDS = ("title", "body")
KEYWORD_FIELDS = ("kind", "category", "tags")
NUMERIC_FIELDS = ("price", "rating")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


@dataclass(frozen=True)
class Token:
    term: str
    position: int
    start: int
    end: int


def analyze(text: str) -> list[Token]:
    # Offsets refer to normalized text, explicitly not the original Unicode byte stream.
    return [
        Token(m[0], i, m.start(), m.end())
        for i, m in enumerate(re.finditer(r"[^\W_]+", normalize(text)))
    ]


def terms(text: str) -> list[str]:
    return [t.term for t in analyze(text)]
