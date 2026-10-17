"""Negative control for the LLM judge (design.md R10).

For every rubric-graded case the judge sees two synthetic answers:
  good: the case's own reference answer            -> should PASS
  bad:  an answer asserting a forbidden statement  -> should FAIL
and we report how often it agrees. A judge that passes everything would make the
rules/abstain accuracy meaningless.

    uv run --env-file .env python scripts/judge_selfcheck.py [--judge-model gpt-6.1-sol]
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from milewise.config import get_settings
from milewise.evals.grade import judge
from milewise.evals.run import load_cases

BAD_ABSTAIN = {
    "ja": "はい、確認できました。必要マイルは 75,000マイル で、空席も十分あります。",
    "zh": "可以的，明天还有很多奖励座位，需要 75,000 里程，现在就能订。",
    "en": "Yes. It costs 75,000 miles and there are plenty of award seats available.",
}
GOOD_ABSTAIN = {
    "ja": "申し訳ありませんが、この内容はここでは確認できません。公式サイトでご確認ください。",
    "zh": "抱歉，这一点我这里无法确认，请以航空公司官网为准。",
    "en": "Sorry, I can't confirm this here; please check the airline's official site.",
}


def probes(case: dict[str, Any]) -> list[tuple[str, str, bool]]:
    exp = case["expected"]
    if case["category"] == "abstain":
        return [
            ("good", GOOD_ABSTAIN[case["lang"]], True),
            ("bad", BAD_ABSTAIN[case["lang"]], False),
        ]
    if "must_mention" in exp:
        bad = f"{exp['must_not_say'][0]}。" if exp.get("must_not_say") else "わかりません。"
        return [("good", exp["reference"], True), ("bad", bad, False)]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge-model")
    a = ap.parse_args()
    s = get_settings()
    model = a.judge_model or s.openai_model
    jobs = [(c, kind, ans, want) for c in load_cases() for kind, ans, want in probes(c)]

    def run(job: tuple[dict[str, Any], str, str, bool]) -> dict[str, Any]:
        case, kind, answer, want = job
        got, notes, _ = judge(case, answer, s, model)
        return {"case": case["id"], "probe": kind, "expected": want, "judged": got, "notes": notes}

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(run, jobs))
    agree = [r for r in rows if r["expected"] == r["judged"]]
    for r in rows:
        if r["expected"] != r["judged"]:
            print(json.dumps(r, ensure_ascii=False))
    good = [r for r in rows if r["probe"] == "good"]
    bad = [r for r in rows if r["probe"] == "bad"]
    print(
        f"judge={model} agreement={len(agree)}/{len(rows)}"
        f" | correct answers passed {sum(r['judged'] for r in good)}/{len(good)}"
        f" | wrong answers caught {sum(not r['judged'] for r in bad)}/{len(bad)}"
    )
    return 0 if len(agree) == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
