from milewise.agent import Evidence, detect_language, postcheck


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
    assert postcheck(answer, evidence()).ok


def test_invented_number_is_flagged() -> None:
    check = postcheck("必要なのは 60,000 miles [C1] です", evidence())
    assert not check.ok and check.unsupported_numbers == [60_000]


def test_chinese_units_are_checked() -> None:
    assert postcheck("需要 45,000 英里 [C1]", evidence()).ok
    assert postcheck("需要 46,000 英里", evidence()).unsupported_numbers == [46_000]
    assert postcheck("需要 46,000 里程", evidence()).unsupported_numbers == [46_000]


def test_unknown_citation_is_flagged() -> None:
    assert postcheck("40,000マイル [C2]", evidence()).unknown_marks == ["C2"]


def test_invented_url_is_flagged() -> None:
    answer = "詳しくは https://www.ana.co.jp/ja/jp/guide/amc/award/ticket/ を参照"
    assert postcheck(answer, evidence()).unknown_urls == [
        "https://www.ana.co.jp/ja/jp/guide/amc/award/ticket/"
    ]


def test_source_url_and_markdown_link_pass() -> None:
    answer = "[ANA](https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/)。"
    assert postcheck(answer, evidence()).ok


def test_url_followed_by_cjk_punctuation_is_not_flagged() -> None:
    # Regression: eval run 46ae7b5c flagged correct answers like these.
    url = "https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/"
    for answer in (
        f"{url}（取得日：2026-10-17）",
        f"{url}，抓取日期：2026-10-17",
        f"{url}；取得日",
    ):
        assert postcheck(answer, evidence()).ok, answer


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
    assert postcheck("48,000マイル [C1] https://www.jal.co.jp/jmb.html", ev).ok
