"""Run the eval set end to end and record the results (design.md §4).

milewise eval                       # all cases, default models, saved to DB + JSONL
milewise eval --model gpt-6.1-sol   # A/B: same cases, stronger answer model
"""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from milewise.agent import SYSTEM_PROMPT, AskResult, ask
from milewise.config import Settings, cost_usd, get_settings
from milewise.db import Db, connect
from milewise.evals.grade import Grade, grade

CASES_PATH = Path(__file__).resolve().parents[3] / "eval" / "cases.yaml"
RESULTS_DIR = CASES_PATH.parent / "results"
THRESHOLDS = {"miles": 0.9, "rules": 0.8, "abstain": 0.8, "citation": 0.8}


@dataclass
class CaseResult:
    case_id: str
    category: str
    lang: str
    passed: bool
    unstable: bool
    answer: str
    tool_calls: list[dict[str, Any]]
    grade: Grade | None
    postcheck_ok: bool
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None = None


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    return list(yaml.safe_load(path.read_text(encoding="utf-8"))["cases"])


def case_sha(case: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


Asker = Callable[[str], AskResult]


def _attempt(case: dict[str, Any], asker: Asker, s: Settings, judge_model: str) -> CaseResult:
    try:
        res = asker(case["question"])
        g = grade(case, res, s, judge_model)
    except Exception as e:  # one broken case must not abort the whole run
        return CaseResult(
            case["id"],
            case["category"],
            case["lang"],
            False,
            False,
            "",
            [],
            None,
            False,
            0,
            0,
            0,
            0.0,
            f"{type(e).__name__}: {e}",
        )
    judge_in = g.judge_usage.get("input_tokens", 0)
    judge_out = g.judge_usage.get("output_tokens", 0)
    judge_cost = cost_usd(judge_model, judge_in, 0, judge_out)
    return CaseResult(
        case_id=case["id"],
        category=case["category"],
        lang=case["lang"],
        passed=g.passed,
        unstable=False,
        answer=res.answer,
        tool_calls=res.tool_calls,
        grade=g,
        postcheck_ok=res.postcheck.ok,
        latency_ms=res.latency_ms,
        input_tokens=res.usage["input_tokens"],
        output_tokens=res.usage["output_tokens"],
        cost_usd=res.cost_usd + judge_cost,
    )


def run_case(
    case: dict[str, Any], s: Settings, asker: Asker, judge_model: str, retries: int
) -> CaseResult:
    first = _attempt(case, asker, s, judge_model)
    if first.passed or retries < 1:
        return first
    # Sampling cannot be pinned (R8): a single failure is re-run once; passing on the
    # retry counts as a pass but is reported as unstable.
    second = _attempt(case, asker, s, judge_model)
    second.unstable = second.passed
    second.cost_usd += first.cost_usd
    return second


def _rate(xs: list[bool]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    def by(*cats: str) -> list[CaseResult]:
        return [r for r in results if r.category in cats]

    miles = [
        r.grade.miles_ok for r in by("miles", "both") if r.grade and r.grade.miles_ok is not None
    ]
    miles += [bool(r.grade.judge_ok) for r in by("miles") if r.grade and r.grade.miles_ok is None]
    rules = [
        bool(r.grade.judge_ok)
        for r in by("rules", "both")
        if r.grade and r.grade.judge_ok is not None
    ]
    abstain = [r.passed for r in by("abstain")]
    citation = [r.grade.citation_ok for r in results if r.grade and r.grade.citation_ok is not None]
    lat = sorted(r.latency_ms for r in results if not r.error)
    return {
        "n_cases": len(results),
        "miles_accuracy": _rate([bool(x) for x in miles]),
        "rules_accuracy": _rate(rules),
        "abstain_accuracy": _rate(abstain),
        "citation_accuracy": _rate([bool(x) for x in citation]),
        "postcheck_fail_rate": _rate([not r.postcheck_ok for r in results if not r.error]),
        "overall_accuracy": _rate([r.passed for r in results]),
        "unstable": [r.case_id for r in results if r.unstable],
        "errors": [r.case_id for r in results if r.error],
        "latency_ms_avg": round(statistics.mean(lat)) if lat else None,
        "latency_ms_p95": lat[max(0, int(len(lat) * 0.95) - 1)] if lat else None,
        "cost_usd_avg": round(sum(r.cost_usd for r in results) / len(results), 6)
        if results
        else None,
        "cost_usd_total": round(sum(r.cost_usd for r in results), 4),
    }


def check_thresholds(summary: dict[str, Any]) -> bool:
    keys = {
        "miles": "miles_accuracy",
        "rules": "rules_accuracy",
        "abstain": "abstain_accuracy",
        "citation": "citation_accuracy",
    }
    return all(summary[keys[k]] is None or summary[keys[k]] >= v for k, v in THRESHOLDS.items())


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True
        ).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except subprocess.CalledProcessError:
        return "uncommitted"


def save(
    db: Db, run: dict[str, Any], cases: list[dict[str, Any]], results: list[CaseResult]
) -> None:
    by_id = {c["id"]: c for c in cases}
    with db.transaction():
        db.execute_many(
            "INSERT INTO eval.cases VALUES (:id, :sha, :cat, :lang, :q, CAST(:exp AS jsonb),"
            " string_to_array(:gold, ' '), CAST(NULLIF(:verified, '') AS date)) ON CONFLICT DO NOTHING",
            [
                {
                    "id": c["id"],
                    "sha": case_sha(c),
                    "cat": c["category"],
                    "lang": c["lang"],
                    "q": c["question"],
                    "exp": json.dumps(c["expected"], ensure_ascii=False),
                    "gold": " ".join(c["gold_urls"]),
                    "verified": str(c.get("verified_at") or ""),
                }
                for c in cases
            ],
        )
        s = run["summary"]
        db.execute(
            "INSERT INTO eval.runs VALUES (CAST(:id AS uuid), CAST(:started AS timestamptz),"
            " CAST(:finished AS timestamptz), :git, :trigger, :target, CAST(:config AS jsonb), :n,"
            " :unverified, :miles, :rules, :abstain, :citation, :postcheck, :overall, :lat, :p95,"
            " :cost_avg, :cost_total, CAST(:thresholds AS jsonb), :passed)",
            {
                "id": run["run_id"],
                "started": run["started_at"],
                "finished": run["finished_at"],
                "git": run["git_sha"],
                "trigger": run["trigger"],
                "target": run["target"],
                "config": json.dumps(run["config"]),
                "n": s["n_cases"],
                "unverified": run["n_unverified"],
                "miles": s["miles_accuracy"],
                "rules": s["rules_accuracy"],
                "abstain": s["abstain_accuracy"],
                "citation": s["citation_accuracy"],
                "postcheck": s["postcheck_fail_rate"],
                "overall": s["overall_accuracy"],
                "lat": s["latency_ms_avg"],
                "p95": s["latency_ms_p95"],
                "cost_avg": s["cost_usd_avg"],
                "cost_total": s["cost_usd_total"],
                "thresholds": json.dumps(THRESHOLDS),
                "passed": run["passed"],
            },
        )
        db.execute_many(
            "INSERT INTO eval.results VALUES (CAST(:run AS uuid), :case, :sha, :answer,"
            " CAST(:tools AS jsonb), string_to_array(:urls, ' '), :miles_ok, :judge_ok, :rationale,"
            " :citation_ok, :postcheck_ok, :passed, :unstable, :lat, :tin, :tout, :cost, :error)",
            [
                {
                    "run": run["run_id"],
                    "case": r.case_id,
                    "sha": case_sha(by_id[r.case_id]),
                    "answer": r.answer,
                    "tools": json.dumps(r.tool_calls, ensure_ascii=False, default=str),
                    "urls": " ".join(r.grade.cited_urls) if r.grade else "",
                    "miles_ok": r.grade.miles_ok if r.grade else None,
                    "judge_ok": r.grade.judge_ok if r.grade else None,
                    "rationale": r.grade.judge_rationale if r.grade else None,
                    "citation_ok": r.grade.citation_ok if r.grade else None,
                    "postcheck_ok": r.postcheck_ok,
                    "passed": r.passed,
                    "unstable": r.unstable,
                    "lat": r.latency_ms,
                    "tin": r.input_tokens,
                    "tout": r.output_tokens,
                    "cost": r.cost_usd,
                    "error": r.error,
                }
                for r in results
            ],
        )


def write_report(run: dict[str, Any], results: list[CaseResult]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{run['started_at'][:19].replace(':', '')}_{run['run_id'][:8]}"
    jsonl = RESULTS_DIR / f"{stem}.jsonl"
    with jsonl.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"run": run}, ensure_ascii=False, default=str) + "\n")
        for r in results:
            f.write(json.dumps(asdict(r), ensure_ascii=False, default=str) + "\n")
    s = run["summary"]

    def pct(x: float | None) -> str:
        return "—" if x is None else f"{x:.0%}"

    lines = [
        f"# Eval run {run['run_id'][:8]} ({run['started_at'][:16]}Z)",
        "",
        f"model `{run['config']['model']}`, judge `{run['config']['judge_model']}`, "
        f"git `{run['git_sha']}`, {s['n_cases']} cases ({run['n_unverified']} not yet human-verified)",
        "",
        "| metric | value | threshold |",
        "|---|---|---|",
        f"| miles accuracy | {pct(s['miles_accuracy'])} | {THRESHOLDS['miles']:.0%} |",
        f"| rules accuracy | {pct(s['rules_accuracy'])} | {THRESHOLDS['rules']:.0%} |",
        f"| abstain accuracy | {pct(s['abstain_accuracy'])} | {THRESHOLDS['abstain']:.0%} |",
        f"| citation accuracy | {pct(s['citation_accuracy'])} | {THRESHOLDS['citation']:.0%} |",
        f"| post-check failures | {pct(s['postcheck_fail_rate'])} | |",
        f"| overall | {pct(s['overall_accuracy'])} | |",
        f"| latency avg / p95 | {s['latency_ms_avg']} / {s['latency_ms_p95']} ms | |",
        f"| cost avg / total | ${s['cost_usd_avg']:.5f} / ${s['cost_usd_total']:.4f} | |",
        "",
        f"**{'PASSED' if run['passed'] else 'FAILED'}**"
        + (f" · unstable: {', '.join(s['unstable'])}" if s["unstable"] else "")
        + (f" · errors: {', '.join(s['errors'])}" if s["errors"] else ""),
        "",
        "| case | cat | lang | result | notes |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        note = r.error or (r.grade.judge_rationale if r.grade and not r.passed else "") or ""
        if r.grade and r.grade.miles_ok is False:
            note = "missing or unbacked figures; " + note
        status = "✅" if r.passed else "❌"
        lines.append(
            f"| {r.case_id} | {r.category} | {r.lang} | {status}{' (unstable)' if r.unstable else ''} "
            f"| {note[:160].replace('|', '/')} |"
        )
    md = RESULTS_DIR / "latest.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonl


def run_eval(
    *,
    model: str | None = None,
    judge_model: str | None = None,
    only: list[str] | None = None,
    workers: int = 4,
    retries: int = 1,
    trigger: str = "local",
    target: str = "local",
    token: str | None = None,
    save_db: bool = True,
) -> dict[str, Any]:
    """target "local" runs the agent in-process; an https URL evaluates a deployed endpoint."""
    s = get_settings()
    model = model or s.openai_model
    judge_model = judge_model or s.openai_model
    cases = [c for c in load_cases() if not only or c["id"] in only]

    asker: Asker
    if target == "local":
        local = threading.local()

        def asker(question: str) -> AskResult:
            if not hasattr(local, "db"):  # psycopg connections are not shared across threads
                local.db = connect()
            return ask(question, local.db, s, model=model)
    else:
        from milewise.evals.remote import ask_remote

        token = token or s.app_token
        if not token:
            raise SystemExit("remote eval needs an app token (APP_TOKEN or --token-secret-arn)")
        model = "deployed"

        def asker(question: str) -> AskResult:
            return ask_remote(target, str(token), question)

    started = datetime.now(UTC).isoformat()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda c: run_case(c, s, asker, judge_model, retries), cases))
    summary = summarize(results)
    run = {
        "run_id": str(uuid.uuid4()),
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "wall_s": round(time.perf_counter() - t0, 1),
        "git_sha": git_sha(),
        "trigger": trigger,
        "target": target,
        "config": {
            "model": model,
            "judge_model": judge_model,
            "reasoning_effort": s.openai_reasoning_effort,
            "embedding_model": s.openai_embedding_model,
            "embedding_dimensions": s.embedding_dimensions,
            "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16],
        },
        "n_unverified": sum(1 for c in cases if not c.get("verified_at")),
        "summary": summary,
        "passed": check_thresholds(summary),
    }
    if save_db:
        save(connect(), run, cases, results)
    run["report"] = str(write_report(run, results))
    return run
