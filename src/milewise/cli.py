"""Command-line entry point.

milewise build-index    chunk + embed the newest raw/ copy of every rules page into build/index
milewise check-charts   parse data/charts/** and print counts (fails on broken files)
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
    p_index = sub.add_parser("build-index", help="build the rules index from raw pages")
    p_index.add_argument("--raw", default="raw", help="directory with nh/ and jl/ raw pages")
    p_index.add_argument("--out", help="output directory (default build/index)")
    p_index.add_argument("--cache", help="previous index whose embeddings may be reused")
    p_index.add_argument(
        "--allow-missing", action="store_true", help="do not fail when a rules page has no raw copy"
    )
    sub.add_parser("check-charts", help="parse the committed award charts and print counts")
    p_ret = sub.add_parser("eval-retrieval", help="hit@k / MRR of vector recall (no LLM)")
    p_ret.add_argument(
        "--embedding-model", help="compare another model, e.g. openai:text-embedding-3-large"
    )
    p_ret.add_argument(
        "--raw", default="raw", help="raw pages for re-indexing with --embedding-model"
    )
    p_ask = sub.add_parser("ask", help="answer a question")
    p_ask.add_argument("question")
    p_ask.add_argument("--model", help="override the chat model (e.g. gpt-6.1-sol)")
    p_ask.add_argument("--json", action="store_true", help="print the full result as JSON")
    p_eval = sub.add_parser("eval", help="run the evaluation set")
    p_eval.add_argument(
        "--model", help="answer model for A/B, e.g. openai:gpt-6-luna (default CHAT_MODEL)"
    )
    p_eval.add_argument("--judge-model", help="judge model (default JUDGE_MODEL)")
    p_eval.add_argument("--only", action="append", help="case id (repeatable)")
    p_eval.add_argument("--workers", type=int, default=4)
    p_eval.add_argument("--no-retry", action="store_true", help="do not re-run failed cases once")
    p_eval.add_argument("--upload", help="also copy the run to this S3 prefix (s3://bucket/eval/)")
    p_eval.add_argument("--trigger", default="local")
    p_eval.add_argument("--target", default="local", help='"local" or a deployed https URL')
    p_eval.add_argument(
        "--token-secret-arn", help="read the app token for --target from Secrets Manager"
    )
    args = parser.parse_args(argv)

    if args.cmd == "build-index":
        return build_index_cmd(args)
    if args.cmd == "eval-retrieval":
        from pathlib import Path

        from milewise.evals.retrieval import run as run_retrieval

        print(
            json.dumps(
                run_retrieval(args.embedding_model, Path(args.raw))["summary"],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.cmd == "check-charts":
        from milewise.charts.store import FareStore

        for name, fam in FareStore.load().families.items():
            print(
                f"{name}: {len(fam.charts)} charts, {len(fam.rates)} rates, "
                f"{len(fam.seasons)} season ranges, {len(fam.zone_groups)} zones"
            )
    elif args.cmd == "eval":
        from milewise.evals.run import run_eval

        token = None
        if args.token_secret_arn:
            import boto3

            sm = boto3.client("secretsmanager")
            token = sm.get_secret_value(SecretId=args.token_secret_arn)["SecretString"]
        run = run_eval(
            target=args.target,
            token=token,
            model=args.model,
            judge_model=args.judge_model,
            only=args.only,
            workers=args.workers,
            retries=0 if args.no_retry else 1,
            trigger=args.trigger,
            upload_to=args.upload,
        )
        print(
            json.dumps(
                {k: run.get(k) for k in ("run_id", "passed", "summary", "report", "uploaded")},
                ensure_ascii=False,
                indent=2,
            )
        )
        print("see eval/results/latest.md", file=sys.stderr)
        return 0 if run["passed"] else 1
    elif args.cmd == "ask":
        from milewise.agent import ask

        res = ask(args.question, model=args.model)
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


def build_index_cmd(args: argparse.Namespace) -> int:
    from pathlib import Path

    from milewise.rag.index import build_index, default_index_dir

    out = Path(args.out) if args.out else default_index_dir()
    cache = Path(args.cache) if args.cache else (out if out.exists() else None)
    rep = build_index(Path(args.raw), out, cache_dir=cache)
    print(
        f"{rep.out_dir}: {rep.n_docs} pages, {rep.n_chunks} chunks, "
        f"{rep.n_embedded} newly embedded, content {rep.content_sha256[:12]}"
    )
    if rep.missing:
        print("missing raw pages:", ", ".join(rep.missing), file=sys.stderr)
        if not args.allow_missing:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
