"""Turn an airline rules page into heading-scoped chunks for retrieval (design.md §2.2).

Only the article body is kept: from the page's <h1> up to the first site-boilerplate
heading (関連タグ, SNS, group info, login dialogs; JAL's category nav); JAL's banner
under the <h1> and its "おすすめ情報" block are dropped. Each chunk is prefixed with its
heading path so a retrieved chunk is understandable on its own and can be cited.
"""

from __future__ import annotations

import hashlib
import html as htmllib
import re
from dataclasses import dataclass

CHUNKER_VERSION = 5  # bump when extraction/chunking changes so pages are re-ingested
MAX_CHARS = 900  # ~300–600 tokens of Japanese text
MIN_CHARS = 200  # smaller sections are merged with their next sibling

# Site boilerplate that ends the article (ANA footer; JAL's site-wide category nav).
STOP_HEADINGS = (
    "関連タグ",
    "ANAグループ企業情報",
    "SNS公式アカウント",
    "会員ログイン",
    "よくあるご質問",
    "JALマイレージバンク",
)
# Promotional blocks inside the article that carry no rules.
SKIP_HEADINGS = ("おすすめ情報", "このページを見た人は")
NAV_LINES = {
    "戻る",
    "進む",
    "特典航空券の一覧へ戻る",
    "ご利用条件",
    "シーズン・必要マイルチャート",
    "お申し込み方法",
    "変更・払い戻し",
    "必要マイル数の確認方法へ戻る",
    "ページトップへ",
}

_H = re.compile(r"<h([1-4])\b[^>]*>(.*?)</h\1>", re.S | re.I)
_HEADING_MARK = "\x00H"


@dataclass(frozen=True)
class Section:
    path: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class Chunk:
    ord: int
    section_path: tuple[str, ...]
    text: str  # heading path + body, as embedded and shown to the model

    def chunk_id(self, doc_key: str) -> str:
        return hashlib.sha256(f"{doc_key}|{self.ord}|{self.text}".encode()).hexdigest()


def _strip(fragment: str) -> str:
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", htmllib.unescape(fragment)).strip()


def _is_intro_nav(path: list[str], text: str) -> bool:
    """Banner / tab bar directly under the <h1>, before the first <h2>: no sentence (。).

    Found on JAL pages. Deliberately limited to the intro: elsewhere, sentence-less
    sections are often tables (area definitions, transfer rules) and must be kept.
    """
    return len(path) == 1 and "。" not in text


def extract_sections(page_html: str) -> list[Section]:
    page_html = page_html.replace("\r\n", "\n").replace("\r", "\n")
    body = re.sub(r"<(script|style|noscript|svg)\b.*?</\1>", " ", page_html, flags=re.S | re.I)
    h1 = re.search(r"<h1\b", body, re.I)
    if h1:
        body = body[h1.start() :]
    else:
        # Feature pages (e.g. JAL's PLUS guide) have no <h1>: use <title> as the root
        # heading and start at the first <h2>.
        title_tag = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.S | re.I)
        h2 = re.search(r"<h2\b", body, re.I)
        if not title_tag or not h2:
            raise ValueError("no <h1>, and no <title> + <h2> to fall back on: not an article page")
        root = re.split(r"\s[-|｜]\s", _strip(title_tag.group(1)))[0]
        body = f"<h1>{root}</h1>" + body[h2.start() :]

    def mark(m: re.Match[str]) -> str:
        return f"\n{_HEADING_MARK}{m.group(1)} {_strip(m.group(2))}\n"

    text = _H.sub(mark, body)
    text = re.sub(r"</t[dh]>", " | ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>|</(p|li|tr|dt|dd|div|ul|ol|table|section)>", "\n", text, flags=re.I)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", text))

    sections: list[Section] = []
    path: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        body_text = "\n".join(buf).strip()
        keep = path and path[-1] not in SKIP_HEADINGS and not _is_intro_nav(path, body_text)
        if body_text and keep:
            sections.append(Section(tuple(path), body_text))
        buf.clear()

    for raw_line in text.split("\n"):
        line = re.sub(r"[ \t　]+", " ", raw_line).strip(" |")
        if not line:
            continue
        if line.startswith(_HEADING_MARK):
            level, title = int(line[2]), line[3:].strip()
            if level > 1 and any(title.startswith(s) for s in STOP_HEADINGS):
                break
            flush()
            path[:] = [*path[: level - 1], title] if level > 1 else [title]
            continue
        if line in NAV_LINES:
            continue
        if buf and buf[-1] in ("*", "※", "・"):
            buf[-1] = f"{buf[-1]} {line}"  # bullet marker rendered on its own line
            continue
        buf.append(line)
    flush()
    return sections


def chunk_sections(sections: list[Section]) -> list[Chunk]:
    chunks: list[Chunk] = []
    pending: Section | None = None

    def emit(path: tuple[str, ...], body: str) -> None:
        header = " > ".join(path)
        chunks.append(Chunk(len(chunks), path, f"{header}\n{body}"))

    for sec in sections:
        if pending is not None:
            # Merge a too-small section into the next one under the same top-level heading.
            if pending.path[:2] == sec.path[:2] and len(pending.text) + len(sec.text) <= MAX_CHARS:
                sec = Section(
                    sec.path[: len(pending.path)]
                    if len(sec.path) > len(pending.path)
                    else sec.path,
                    f"【{pending.path[-1]}】\n{pending.text}\n【{sec.path[-1]}】\n{sec.text}",
                )
            else:
                emit(pending.path, pending.text)
            pending = None
        if len(sec.text) < MIN_CHARS:
            pending = sec
            continue
        # Split long sections on line boundaries.
        part: list[str] = []
        size = 0
        for line in sec.text.split("\n"):
            if part and size + len(line) > MAX_CHARS:
                emit(sec.path, "\n".join(part))
                part, size = [], 0
            part.append(line)
            size += len(line) + 1
        if part:
            emit(sec.path, "\n".join(part))
    if pending is not None:
        emit(pending.path, pending.text)
    return chunks
