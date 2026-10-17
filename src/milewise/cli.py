"""Command-line entry point.

milewise migrate        apply db/migrations
milewise load-charts    load data/charts/** into the fare schema
milewise ask "質問"     answer one question end to end
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from milewise import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="milewise")
    parser.add_argument("--version", action="version", version=f"milewise {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("load-charts", help="load committed award charts into the database")
    sub.add_parser("ingest-local", help="ingest rules pages from raw/ into rag.*")
    p_ask = sub.add_parser("ask", help="answer a question")
    p_ask.add_argument("question")
    p_ask.add_argument("--model", help="override the chat model (e.g. gpt-6.1-sol)")
    p_ask.add_argument("--json", action="store_true", help="print the full result as JSON")
    p_eval = sub.add_parser("eval", help="run the evaluation set")
    p_eval.add_argument("--model", help="answer model (A/B), default OPENAI_MODEL")
    p_eval.add_argument("--judge-model", help="judge model, default OPENAI_MODEL")
    p_eval.add_argument("--only", action="append", help="case id (repeatable)")
    p_eval.add_argument("--workers", type=int, default=4)
    p_eval.add_argument("--no-retry", action="store_true", help="do not re-run failed cases once")
    p_eval.add_argument(
        "--no-save", action="store_true", help="skip writing results to the database"
    )
    p_eval.add_argument("--trigger", default="local")
    args = parser.parse_args(argv)

    from milewise.db import connect, migrate

    db = connect()
    if args.cmd == "migrate":
        print("applied:", migrate(db) or "nothing (up to date)")
    elif args.cmd == "load-charts":
        from milewise.charts.load import load_all

        for family, counts in load_all(db).items():
            print(f"{family}: {counts}")
    elif args.cmd == "ingest-local":
        ingest_local(db)
    elif args.cmd == "eval":
        from milewise.evals.run import run_eval

        run = run_eval(
            model=args.model,
            judge_model=args.judge_model,
            only=args.only,
            workers=args.workers,
            retries=0 if args.no_retry else 1,
            trigger=args.trigger,
            save_db=not args.no_save,
        )
        print(
            json.dumps(
                {k: run[k] for k in ("run_id", "passed", "summary", "report")},
                ensure_ascii=False,
                indent=2,
            )
        )
        print("see eval/results/latest.md", file=sys.stderr)
        return 0 if run["passed"] else 1
    elif args.cmd == "ask":
        from milewise.agent import ask

        res = ask(args.question, db, model=args.model)
        if args.json:
            print(json.dumps(asdict(res), ensure_ascii=False, indent=2, default=str))
        else:
            print(res.answer)
            print(
                f"\n---\nmodel={res.model} latency={res.latency_ms}ms "
                f"tokens in/out={res.usage['input_tokens']}/{res.usage['output_tokens']} "
                f"calls={res.usage['model_calls']} cost=${res.cost_usd:.5f} "
                f"tools={[t['name'] for t in res.tool_calls]} postcheck={'ok' if res.postcheck.ok else res.postcheck}",
                file=sys.stderr,
            )
    return 0


def ingest_local(db: object) -> None:
    import hashlib
    import re
    from datetime import datetime
    from pathlib import Path

    import yaml

    from milewise.config import data_dir, get_settings
    from milewise.rag.ingest import ingest_page

    cfg = yaml.safe_load((data_dir() / "sources.yaml").read_text(encoding="utf-8"))
    failures = 0
    for src in cfg["sources"]:
        if src["kind"] != "rules":
            continue
        files = sorted(Path("raw", src["airline"].lower()).glob(f"{src['id']}_*.html"))
        if not files:
            print(f"missing  {src['id']}: no raw/{src['airline'].lower()}/{src['id']}_*.html")
            continue
        path = files[-1]
        raw = path.read_bytes()
        ts = re.search(r"(\d{8}T\d{6})", path.name).group(1)  # type: ignore[union-attr]
        fetched = datetime.strptime(ts, "%Y%m%dT%H%M%S").astimezone().isoformat()
        try:
            res = ingest_page(
                db,  # type: ignore[arg-type]
                raw.decode("utf-8"),
                source=src,
                raw_key=str(path),
                raw_etag=hashlib.sha256(raw).hexdigest(),
                fetched_at=fetched,
                embedding_model=get_settings().openai_embedding_model,
            )
        except Exception as e:  # one bad page must not stop the rest (as in the Lambda)
            failures += 1
            print(f"FAILED                {src['id']}: {type(e).__name__}: {e}")
            continue
        print(f"{res.status:<21} {src['id']}: {res.chunks_written} chunks")
    if failures:
        raise SystemExit(f"{failures} page(s) failed to ingest")


if __name__ == "__main__":
    sys.exit(main())
