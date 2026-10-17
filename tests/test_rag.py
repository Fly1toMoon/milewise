from __future__ import annotations

import hashlib
from collections.abc import Sequence

from milewise.db import Db
from milewise.rag.chunking import chunk_sections, extract_sections
from milewise.rag.ingest import ingest_page

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


def ingest(db: Db, html: str, etag: str) -> str:
    return ingest_page(
        db,
        html,
        source=SOURCE,
        raw_key="raw/nh/test.html",
        raw_etag=etag,
        fetched_at="2026-10-17T00:00:00+09:00",
        embedding_model="fake",
        embed_fn=fake_embed,
    ).status


def test_ingest_is_idempotent_and_versions_documents(db: Db) -> None:
    assert ingest(db, PAGE, "e1") == "succeeded"
    assert ingest(db, PAGE, "e1") == "duplicate"  # same object delivered twice
    assert ingest(db, PAGE, "e2") == "skipped_same_content"  # re-uploaded, same text
    assert ingest(db, PAGE.replace("3,000", "4,000"), "e3") == "succeeded"  # page changed

    docs = db.query(
        "SELECT is_current, superseded_at IS NOT NULL AS superseded FROM rag.documents"
        " WHERE source_url = :u ORDER BY fetched_at, superseded_at NULLS LAST",
        {"u": SOURCE["url"]},
    )
    assert sorted((d["is_current"], d["superseded"]) for d in docs) == [
        (False, True),
        (True, False),
    ]
    current_text = db.query(
        "SELECT string_agg(c.text, ' ') AS t FROM rag.chunks c JOIN rag.documents d USING (doc_id)"
        " WHERE d.source_url = :u AND d.is_current",
        {"u": SOURCE["url"]},
    )[0]["t"]
    assert "4,000マイル" in current_text and "3,000マイル" not in current_text


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


def test_notice_repeated_across_pages_is_stored_whole_but_retrieved_once(db: Db) -> None:
    from milewise.rag.search import recall_by_vector

    a = {"id": "dedup-a", "airline": "JL", "url": "https://example.test/a", "lang": "ja"}
    b = {"id": "dedup-b", "airline": "JL", "url": "https://example.test/b", "lang": "ja"}
    for src, page in (
        (a, _page("ページA", "ご注意", "Aだけの規則です。")),
        (b, _page("ページB", "お知らせ", "Bだけの規則です。")),
    ):
        status = ingest_page(
            db,
            page,
            source=src,
            raw_key=f"raw/jl/{src['id']}.html",
            raw_etag="e",
            fetched_at="2026-10-17T00:00:00+09:00",
            embedding_model="fake",
            embed_fn=fake_embed,
        ).status
        assert status == "succeeded"

    stored = db.query(
        "SELECT count(*) AS n FROM rag.chunks c JOIN rag.documents d USING (doc_id)"
        " WHERE d.is_current AND d.source_url LIKE 'https://example.test/_' AND c.text LIKE '%カード決済%'"
    )[0]["n"]
    assert stored == 2  # both pages keep their own copy: no ingestion-order dependence

    [query_vec] = fake_embed(
        [
            "ページA > ご注意\nシステム調整のため停止しておりました取消手数料のカード決済を再開しました。"
        ]
    )
    rows = recall_by_vector(db, query_vec, "JL", k=20)
    notices = [r for r in rows if "カード決済" in r["text"]]
    assert len(notices) == 1  # ...but retrieval returns the notice once
    assert {r["text"].split("\n")[0] for r in rows} >= {"ページA > 固有", "ページB > 固有"}
