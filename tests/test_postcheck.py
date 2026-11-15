from milewise.evidence import Evidence
from milewise.prompt import detect_language
from milewise.verify import verify


def values(answer: str, ev: Evidence, kind: str) -> list[str]:
    return [i.quote for i in verify(answer, ev) if i.kind == kind]


def evidence() -> Evidence:
    ev = Evidence()
    ev.add_chart(
        {
            "results": [{"season": "L", "miles": 40_000}, {"season": "R", "miles": 50_000}],
            "total_miles": 45_000,
            "chart": {
                "source_url": "https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/"
            },
        }
    )
    return ev


def test_numbers_from_evidence_pass() -> None:
    answer = "往復 45,000マイル [C1]（40,000マイル÷2=20,000マイル + 50,000マイル÷2=25,000マイル）"
    assert verify(answer, evidence()) == []


def test_invented_number_is_flagged() -> None:
    assert values("必要なのは 60,000 miles [C1] です", evidence(), "number") == ["60,000 miles"]


def test_chinese_units_are_checked() -> None:
    assert verify("需要 45,000 英里 [C1]", evidence()) == []
    assert values("需要 46,000 英里", evidence(), "number") == ["46,000 英里"]
    assert values("需要 46,000 里程", evidence(), "number") == ["46,000 里程"]


def test_unknown_citation_is_flagged() -> None:
    assert values("40,000マイル [C2]", evidence(), "citation") == ["[C2]"]


def test_invented_url_is_flagged() -> None:
    answer = "詳しくは https://www.ana.co.jp/ja/jp/guide/amc/award/ticket/ を参照"
    assert values(answer, evidence(), "url") == [
        "https://www.ana.co.jp/ja/jp/guide/amc/award/ticket/"
    ]


def test_source_url_and_markdown_link_pass() -> None:
    answer = "[ANA](https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/)。"
    assert verify(answer, evidence()) == []


def test_url_followed_by_cjk_punctuation_is_not_flagged() -> None:
    # Regression: eval run 46ae7b5c flagged correct answers like these.
    url = "https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/"
    for answer in (
        f"{url}（取得日：2026-10-17）",
        f"{url}，抓取日期：2026-10-17",
        f"{url}；取得日",
    ):
        assert verify(answer, evidence()) == [], answer


def test_detect_language() -> None:
    assert detect_language("東京→ロンドンは何マイル？") == "ja"
    assert detect_language("从东京去洛杉矶要多少里程？") == "zh"
    assert detect_language("How many miles from Tokyo to London?") == "en"


def test_every_chart_of_a_jal_partner_result_may_be_linked() -> None:
    ev = Evidence()
    ev.add_chart(
        {
            "results": [{"miles": 48_000}],
            "chart": {
                "source_url": "https://www.jal.co.jp/ow.html",
                "source_urls": ["https://www.jal.co.jp/ow.html", "https://www.jal.co.jp/jmb.html"],
            },
        }
    )
    assert verify("48,000マイル [C1] https://www.jal.co.jp/jmb.html", ev) == []


def test_full_width_marks_are_normalised() -> None:
    from milewise.agent import prior_turns
    from milewise.evidence import normalize_marks

    assert normalize_marks("不可以【D1】，见［D2］和[ C3 ]") == "不可以[D1]，见[D2]和[C3]"
    turns = prior_turns(
        [{"role": "user", "content": "q"}, {"role": "assistant", "content": "答【D1】。"}]
    )
    assert turns[1]["content"] == "答。"


def test_full_width_marks_count_as_citations() -> None:
    from milewise.agent import AskResult
    from milewise.evals.grade import cited_urls, quote_in

    res = AskResult(
        "q",
        "不能【D1】",
        {"D1": {"url": "https://x.example/"}},
        True,
        [],
        {},
        0.0,
        0,
        "m",
    )
    assert cited_urls(res) == ["https://x.example/"]
    assert quote_in("不能【D1】再去第三国", "不能再去第三国")
