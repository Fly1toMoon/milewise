"""Run the eval set end to end and record the results (design.md §4).

milewise eval                       # all cases, default models, JSONL + latest.md
milewise eval --model gpt-6.1-sol   # A/B: same cases, stronger answer model
milewise eval --upload s3://bucket/eval/   # also keep the run in S3 (CI does this)
milewise eval --only multi-05 --repeat 5   # each case 5 times, reported as passes/5
"""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from milewise.agent import AskResult, ask
from milewise.config import Settings, cost_usd, get_settings
from milewise.evals.grade import Grade, grade
from milewise.prompt import SYSTEM_PROMPT

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
    first_attempt: str | None = None  # why an unstable case failed the first time
    attempt: int = 0  # 0..N-1 with --repeat N
    check: dict[str, Any] | None = None  # verification of the answer (AskResult.check)
    model: str = ""  # the model that wrote the answer (AskResult.model; "" on an error)


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    return list(yaml.safe_load(path.read_text(encoding="utf-8"))["cases"])


def case_sha(case: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# (question, earlier turns of the conversation or None)
Asker = Callable[[str, list[dict[str, str]] | None], AskResult]


def _attempt(case: dict[str, Any], asker: Asker, s: Settings, judge_model: str) -> CaseResult:
    try:
        res = asker(case["question"], case.get("history"))
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
        postcheck_ok=res.postcheck_ok,
        latency_ms=res.latency_ms,
        input_tokens=res.usage["input_tokens"],
        output_tokens=res.usage["output_tokens"],
        cost_usd=res.cost_usd + judge_cost,
        check=res.check,
        model=res.model,
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
    second.first_attempt = first.error or (
        "figures missing or unbacked; " if first.grade and first.grade.miles_ok is False else ""
    ) + (first.grade.judge_rationale or "" if first.grade else "") + (
        "" if first.postcheck_ok else " | post-check failed"
    )
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
    revised = [r.check for r in results if r.check and r.check["revised"]]
    by_model: dict[str, list[CaseResult]] = {}
    for r in results:
        if not r.error:
            by_model.setdefault(r.model, []).append(r)
    per_case: dict[str, list[int]] = {}
    for r in results:
        k = per_case.setdefault(r.case_id, [0, 0])
        k[0] += r.passed
        k[1] += 1
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
        # passes / runs per case (more than one run per case with --repeat)
        "per_case": {k: f"{p}/{n}" for k, (p, n) in per_case.items()},
        # verification (design.md §4.13): how often an answer was rewritten, and what verify
        # plus rewrite took per rewritten question
        "checks": {
            "revised": len(revised),
            "revise_latency_ms_avg": round(statistics.mean(c["latency_ms"] for c in revised))
            if revised
            else None,
        },
        # questions each model answered (ground.answer_model), with their average cost (the
        # judge's call included, as in cost_usd_avg) and latency; errors are not counted
        "by_model": {
            m: {
                "n": len(rs),
                "cost_usd_avg": round(statistics.mean(r.cost_usd for r in rs), 6),
                "latency_ms_avg": round(statistics.mean(r.latency_ms for r in rs)),
            }
            for m, rs in sorted(by_model.items())
        },
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


def upload(prefix: str, jsonl: Path) -> list[str]:
    """Copy the run's JSONL and report to S3 (the eval history; design.md §4.6)."""
    import boto3

    bucket, _, key = prefix.removeprefix("s3://").partition("/")
    key = key.rstrip("/")
    s3 = boto3.client("s3")
    out = []
    for path, name in (
        (jsonl, f"runs/{jsonl.name}"),
        (RESULTS_DIR / "latest.md", f"reports/{jsonl.stem}.md"),
    ):
        dest = f"{key}/{name}" if key else name
        s3.upload_file(str(path), bucket, dest)
        out.append(f"s3://{bucket}/{dest}")
    return out


def data_version(target: str) -> dict[str, Any] | None:
    """The rules index the answers came from: in-process, or the deployed /healthz."""
    if target == "local":
        from milewise.api import data_version as local_version

        return local_version()
    import httpx

    try:
        return dict(httpx.get(f"{target.rstrip('/')}/healthz", timeout=30).json())
    except (httpx.HTTPError, ValueError):
        return None


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
        f"model `{run['config']['model']}`"
        + (
            f" (follow-ups to rule answers: `{run['config']['follow_up_model']}`)"
            if run["config"].get("follow_up_model")
            else ""
        )
        + f", judge `{run['config']['judge_model']}`, "
        f"git `{run['git_sha']}`, {s['n_cases']} cases ({run['n_unverified']} not yet human-verified)"
        + (f", each run {run['repeat']} times" if run.get("repeat", 1) > 1 else ""),
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
        f"| rewritten after the check | {s['checks']['revised']} | |",
        "",
        "| answered by | questions | cost avg | latency avg |",
        "|---|---|---|---|",
        *(
            f"| `{m}` | {v['n']} | ${v['cost_usd_avg']:.5f} | {v['latency_ms_avg']} ms |"
            for m, v in s["by_model"].items()
        ),
        "",
        f"**{'PASSED' if run['passed'] else 'FAILED'}**"
        + (f" · unstable: {', '.join(s['unstable'])}" if s["unstable"] else "")
        + (f" · errors: {', '.join(s['errors'])}" if s["errors"] else ""),
        "",
    ]
    if run.get("repeat", 1) > 1:
        lines += ["| case | passed |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in s["per_case"].items()]
        lines.append("")
    lines += ["| case | cat | lang | model | result | notes |", "|---|---|---|---|---|---|"]
    for r in results:
        note = r.error or (r.grade.judge_rationale if r.grade and not r.passed else "") or ""
        if r.grade and r.grade.check_notes:
            note = "; ".join(r.grade.check_notes) + " | " + note
        if r.grade and r.grade.miles_ok is False:
            note = "missing or unbacked figures; " + note
        if r.unstable and r.first_attempt:
            note = f"1st try failed: {r.first_attempt}"
        status = "✅" if r.passed else "❌"
        lines.append(
            f"| {r.case_id}{f' #{r.attempt + 1}' if run.get('repeat', 1) > 1 else ''} | {r.category} | {r.lang} | {r.model.partition(':')[2] or r.model} | {status}{' (unstable)' if r.unstable else ''} "
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
    upload_to: str | None = None,
    repeat: int = 1,
) -> dict[str, Any]:
    """target "local" runs the agent in-process; an https URL evaluates a deployed endpoint.

    With repeat > 1 every case runs that many times (no retries) and the report gives each
    case's passes / runs: one run per case cannot tell a fix from sampling noise."""
    s = get_settings()
    judge_model = judge_model or s.judge_model
    cases = [c for c in load_cases() if not only or c["id"] in only]

    asker: Asker
    if target == "local":
        # without --model each question goes to the model ground.answer_model picks
        def asker(question: str, history: list[dict[str, str]] | None) -> AskResult:
            return ask(question, settings=s, model=model, history=history)

        config_models = {
            "model": model or s.chat_model,
            "follow_up_model": None if model else s.follow_up_model,
        }
    else:
        from milewise.evals.remote import ask_remote

        token = token or s.app_token
        if not token:
            raise SystemExit("remote eval needs an app token (APP_TOKEN or --token-secret-arn)")
        config_models = {"model": "deployed", "follow_up_model": None}

        def asker(question: str, history: list[dict[str, str]] | None) -> AskResult:
            return ask_remote(target, str(token), question, history=history)

    started = datetime.now(UTC).isoformat()
    t0 = time.perf_counter()
    if repeat > 1:
        retries = 0

    def one(job: tuple[dict[str, Any], int]) -> CaseResult:
        r = run_case(job[0], s, asker, judge_model, retries)
        r.attempt = job[1]
        return r

    jobs = [(c, i) for c in cases for i in range(repeat)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, jobs))
    summary = summarize(results)
    run = {
        "run_id": str(uuid.uuid4()),
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "wall_s": round(time.perf_counter() - t0, 1),
        "git_sha": git_sha(),
        "trigger": trigger,
        "target": target,
        "repeat": repeat,
        "config": {
            **config_models,
            "judge_model": judge_model,
            "rerank_model": s.rerank_model,
            "reasoning_effort": s.reasoning_effort,
            "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16],
            "data": data_version(target),
        },
        "n_unverified": sum(1 for c in cases if not c.get("verified_at")),
        "summary": summary,
        "passed": check_thresholds(summary),
    }
    jsonl = write_report(run, results)
    run["report"] = str(jsonl)
    if upload_to:
        run["uploaded"] = upload(upload_to, jsonl)
    return run
