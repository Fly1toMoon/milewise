from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from milewise.config import Settings
from milewise.rag.chunking import chunk_sections, extract_sections
from milewise.rag.index import RulesIndex, build_index

PAGE = """<html><body>
<nav><a>トップ</a> <a>国内</a></nav>
<h1>テスト特典 変更・払い戻し</h1>\r
<ul><li>戻る</li><li>ご利用条件</li><li>進む</li></ul>
<h2>未使用特典の払い戻し</h2>
<p>すべての区間が未使用の場合に限り、マイルを口座にお戻しします。</p>
<table><tr><th>手数料</th><td>1名様につき3,000マイル</td></tr></table>
<h3>受付期間</h3>
<ul><li>*</li><li>有効期間満了日より30日以内</li></ul>
<h2>関連タグ</h2><p>ここから先はサイト共通部分</p>
</body></html>"""

SOURCE = {"id": "test-refund", "airline": "NH", "url": "https://example.test/refund", "lang": "ja"}


def fake_embed(texts: Sequence[str]) -> list[list[float]]:
    out = []
    for t in texts:
        seed = hashlib.sha256(t.encode()).digest()
        out.append([(seed[i % 32] - 128) / 128 for i in range(1024)])
    return out


def test_extract_sections_keeps_article_only() -> None:
    secs = extract_sections(PAGE)
    assert [s.path for s in secs] == [
        ("テスト特典 変更・払い戻し", "未使用特典の払い戻し"),
        ("テスト特典 変更・払い戻し", "未使用特典の払い戻し", "受付期間"),
    ]
    body = "\n".join(s.text for s in secs)
    assert "3,000マイル" in body and "手数料" in body
    assert "サイト共通部分" not in body and "トップ" not in body and "戻る" not in body
    assert "* 有効期間満了日より30日以内" in body  # bullet joined to its text
    assert "\r" not in body


def test_chunks_carry_heading_path() -> None:
    chunks = chunk_sections(extract_sections(PAGE))
    assert chunks and all(c.text.startswith("テスト特典 変更・払い戻し > ") for c in chunks)


class CountingEmbed:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        return fake_embed(texts)


def write_site(tmp: Path, pages: dict[str, tuple[dict[str, str], str]]) -> Path:
    """sources.yaml + raw/<airline>/<id>_<ts>.html, as fetch/import scripts lay them out."""
    sources = []
    for name, (src, html) in pages.items():
        sources.append({**src, "id": name, "kind": "rules"})
        d = tmp / "raw" / src["airline"].lower()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}_20261017T100000.html").write_text(html, encoding="utf-8")
    (tmp / "sources.yaml").write_text(yaml.safe_dump({"sources": sources}), encoding="utf-8")
    return tmp / "sources.yaml"


SETTINGS = Settings(openai_api_key="x", openai_embedding_model="fake", embedding_dimensions=1024)


def build(tmp: Path, sources: Path, embed: CountingEmbed, cache: Path | None = None) -> RulesIndex:
    build_index(
        tmp / "raw",
        tmp / "index",
        cache_dir=cache,
        sources_path=sources,
        embed_fn=embed,
        settings=SETTINGS,
    )
    return RulesIndex.load(tmp / "index")


def test_index_build_reuses_cached_embeddings_and_is_deterministic(tmp_path: Path) -> None:
    sources = write_site(tmp_path, {"test-refund": (SOURCE, PAGE)})
    embed = CountingEmbed()
    first = build(tmp_path, sources, embed)
    assert first.manifest["n_chunks"] == len(first.chunks) > 0 and embed.calls == [
        len(first.chunks)
    ]
    assert (
        first.chunks[0]["url"] == SOURCE["url"] and first.chunks[0]["fetched_date"] == "2026-10-17"
    )

    again = build(tmp_path, sources, embed, cache=tmp_path / "index")
    assert embed.calls == [len(first.chunks)]  # nothing re-embedded
    assert again.manifest["content_sha256"] == first.manifest["content_sha256"]

    write_site(tmp_path, {"test-refund": (SOURCE, PAGE.replace("3,000", "4,000"))})
    changed = build(tmp_path, sources, embed, cache=tmp_path / "index")
    assert embed.calls[1:] == [1]  # only the changed chunk
    text = " ".join(c["text"] for c in changed.chunks)
    assert "4,000マイル" in text and "3,000マイル" not in text


def test_missing_raw_page_is_reported(tmp_path: Path) -> None:
    sources = write_site(tmp_path, {"test-refund": (SOURCE, PAGE)})
    cfg = yaml.safe_load(sources.read_text(encoding="utf-8"))
    cfg["sources"].append({**SOURCE, "id": "not-saved", "kind": "rules"})
    sources.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    rep = build_index(
        tmp_path / "raw",
        tmp_path / "index",
        sources_path=sources,
        embed_fn=fake_embed,
        settings=SETTINGS,
    )
    assert rep.missing == ["not-saved"] and rep.n_docs == 1


def test_load_without_index_says_how_to_build_it(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="make index"):
        RulesIndex.load(tmp_path)


JAL_LIKE = """<body><h1 class="heading">JAL国際線特典航空券</h1>
<ul><li>片道7,500マイル 往復15,000マイルから世界の旅に交換</li><li>申し込み方法</li><li>必要マイル早見表</li></ul>
<h2>おすすめ情報</h2><ul><li>当日、空港でもマイルをつかって快適！便利！</li></ul>
<h2 class="heading">予約の変更</h2>
<p>予約変更は承れません。特典航空券の払い戻し時は、航空券1冊につき3,100円の取消手数料を申し受けます。</p>
<h2>各エリアの区分</h2>
<ul><li>エリア 1</li><li>北米・中米・南米、ハワイなど</li><li>エリア 2</li><li>欧州、中東、アフリカなど</li></ul>
<h2 class="categoryNav_category">JALマイレージバンク</h2><ul><li>JALマイレージバンクとは</li></ul>
<p>このサイトでは、お客さまに適したお得な商品やサービスの案内…を利用する場合があります。</p>
</body>"""


def test_jal_layout_keeps_rules_and_drops_banner_promo_and_site_nav() -> None:
    secs = extract_sections(JAL_LIKE)
    titles = [s.path[-1] for s in secs]
    assert titles == ["予約の変更", "各エリアの区分"]  # banner, おすすめ情報, site nav removed
    body = "\n".join(s.text for s in secs)
    assert "3,100円" in body
    assert "北米・中米・南米" in body  # sentence-less tables are content, not navigation
    assert "このサイトでは" not in body and "片道7,500マイル" not in body


def test_page_without_h1_falls_back_to_title() -> None:
    page = """<html><head><title>JAL国際線特典航空券PLUSとは - JALマイレージバンク</title></head>
    <body><nav>メニュー</nav><h2>ポイント1</h2><p>基本マイルの席がない場合も、追加マイルで予約できます。</p></body></html>"""
    secs = extract_sections(page)
    assert [s.path for s in secs] == [("JAL国際線特典航空券PLUSとは", "ポイント1")]
    assert "メニュー" not in secs[0].text


def _page(title: str, notice_heading: str, own: str) -> str:
    return (
        f"<h1>{title}</h1>"
        f"<h2>{notice_heading}</h2><p>システム調整のため停止しておりました取消手数料のカード決済を再開しました。</p>"
        f"<h2>固有</h2><p>{own}</p>"
    )


def test_notice_repeated_across_pages_is_stored_whole_but_retrieved_once(
    tmp_path: Path,
) -> None:
    a = {"airline": "JL", "url": "https://example.test/a", "lang": "ja"}
    b = {"airline": "JL", "url": "https://example.test/b", "lang": "ja"}
    other = {"airline": "NH", "url": "https://example.test/nh", "lang": "ja"}
    sources = write_site(
        tmp_path,
        {
            "dedup-a": (a, _page("ページA", "ご注意", "Aだけの規則です。")),
            "dedup-b": (b, _page("ページB", "お知らせ", "Bだけの規則です。")),
            "nh-page": (other, _page("ANAページ", "ご注意", "ANAの規則です。")),
        },
    )
    index = build(tmp_path, sources, CountingEmbed())
    stored = [c for c in index.chunks if "カード決済" in c["text"]]
    assert len(stored) == 3  # every page keeps its own copy: no ingestion-order dependence

    [query_vec] = fake_embed(
        [
            "ページA > ご注意\nシステム調整のため停止しておりました取消手数料のカード決済を再開しました。"
        ]
    )
    rows = index.recall(query_vec, "JL", k=20)
    notices = [r for r in rows if "カード決済" in r["text"]]
    assert len(notices) == 1 and notices[0]["source_url"] == "https://example.test/a"
    assert {r["text"].split("\n")[0] for r in rows} == {
        "ページA > 固有",
        "ページB > 固有",
        "ページA > ご注意",
    }
    assert all(r["airline"] == "JL" for r in rows)
    assert rows[0]["score"] == pytest.approx(1.0, abs=1e-5)
