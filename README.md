# Milewise

Award-ticket (mileage) assistant for **ANA Mileage Club** and **JAL Mileage Bank**, running on AWS Lambda with Amazon Bedrock (Nova 2 Lite; Titan embeddings). Award charts and the rules index ship inside the container image, so data changes go through the same eval gate as code.

- Required miles are **computed by code** from structured award charts (no RAG guessing on numbers): ANA and JAL own-metal charts, ANA's partner-airline zone chart and JAL's distance-based partner charts.
- Rules, change and refund conditions are answered with **retrieval + citations** to the airlines' official pages.
- Every answer states its evidence (chart version / source URL), and says "not sure" when evidence is missing.

> Work in progress. Design: [docs/design.md](docs/design.md) (Chinese). Setup: [docs/prerequisites.md](docs/prerequisites.md).

## Repository layout

```
src/milewise/          application (FastAPI, agent, tools)
data/charts/           structured award charts (CSV + source URL + fetch date), read directly at runtime
data/sources.yaml      the official pages indexed for rules questions; raw pages are never committed
build/index/           rules index built from raw pages (gitignored, shipped in the image)
eval/                  evaluation cases and results
infra/bootstrap/       persistent Terraform stack: state bucket, budgets, ECR, buckets, GitHub OIDC
infra/app/             destroyable stack: Lambda + Function URLs, DynamoDB usage table, secrets, alarms
infra/spikes/          throwaway experiments that validate design assumptions
scripts/               crawlers, parsers, preflight checks
```

## Quick start (local)

```bash
make setup                 # Python 3.12 deps via uv
cp .env.example .env       # then set OPENAI_API_KEY
make test                  # unit tests: parsers, miles golden values, index, post-check
make import-raw index      # saved pages -> raw/ -> build/index (chunks + embeddings)
make ask Q="東京→ロンドンのビジネスクラス特典航空券は何マイル必要？"
```

## How it evolved

The first version stored everything in Aurora PostgreSQL Serverless v2 with pgvector, reached
from Lambda through the RDS Data API (no VPC, no NAT). Measured at real size (305 chunks,
~840 chart rows) that was the wrong tool: a paused cluster took ~20 s to answer the first
question, an always-on one cost ~$55/month, and the ingest pipeline wrote straight into the
live database, so new documents bypassed the eval gate. The data now ships in the image and is
searched in memory (<3 ms per query); see design.md §4.6 for the trade-offs.

## Evaluation (baseline, 2026-10-18)

32 cases (miles 18 / rules 9 / both 2 / abstain 3; ja, zh, en), gold answers taken from the
airlines' official pages, not from Milewise's own tools. Rules and abstain cases are graded by an
LLM judge (Nova Pro, a different model from the answering one) with a key-point rubric; the
judge's negative control agrees 42/42.

| answer model | miles | rules | abstain | citation | overall | latency avg | cost / question |
|---|---|---|---|---|---|---|---|
| `bedrock:jp.amazon.nova-2-lite-v1:0` (default) | 100% | 100% | 100% | 100% | 100% | 7.6 s | $0.0093 |
| `openai:gpt-6-luna` | 94% | 100% | 100% | 96% | 97% | 5.9 s | $0.0021 |

```bash
make eval                                                     # writes eval/results/latest.md
uv run --env-file .env milewise eval --model openai:gpt-6-luna  # A/B against OpenAI
```

Gold answers are still awaiting human verification (`verified_at` in `eval/cases.yaml`).
