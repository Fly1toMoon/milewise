# Milewise

Award-ticket (mileage) assistant for **ANA Mileage Club** and **JAL Mileage Bank**, running on AWS (Lambda, Aurora Serverless v2 + pgvector) with the OpenAI API.

- Required miles are **computed by code** from structured award charts (no RAG guessing on numbers).
- Rules, change and refund conditions are answered with **retrieval + citations** to the airlines' official pages.
- Every answer states its evidence (chart version / source URL), and says "not sure" when evidence is missing.

> Work in progress. Design: [docs/design.md](docs/design.md) (Chinese). Setup: [docs/prerequisites.md](docs/prerequisites.md).

## Repository layout

```
src/milewise/          application (FastAPI, agent, tools)
db/migrations/         plain SQL migrations (local Postgres and Aurora via Data API)
data/charts/           structured award charts (CSV + source URL + fetch date); raw pages are never committed
eval/                  evaluation cases and results
infra/bootstrap/       persistent Terraform stack: state bucket, budgets, ECR, buckets, GitHub OIDC
infra/spikes/          throwaway experiments that validate design assumptions
scripts/               crawlers, parsers, preflight checks
```

## Quick start (local)

```bash
make setup                 # Python 3.12 deps via uv
cp .env.example .env       # then set OPENAI_API_KEY
make db-up                 # local Postgres 16 + pgvector
make migrate load-charts   # schema + committed award charts
make test                  # 40 tests: parser, city resolution, miles golden values, post-check
make ask Q="東京→ロンドンのビジネスクラス特典航空券は何マイル必要？"
```

## Evaluation (baseline, 2026-10-17)

20 cases (miles 8 / rules 7 / both 2 / abstain 3; ja 10, zh 6, en 4), gold answers taken from the
airlines' official pages, not from Milewise's own tools. Rules and abstain cases are graded by an
LLM judge with a key-point rubric; the judge's negative control agrees 24/24.

| answer model | miles | rules | abstain | citation | overall | latency avg | cost / question |
|---|---|---|---|---|---|---|---|
| `gpt-6-luna` (default) | 100% | 89% | 100% | 100% | 95% | 6.3 s | $0.00087 |
| `gpt-6.1-sol` | 100% | 100% | 100% | 100% | 100% | 13.1 s | $0.0110 |

```bash
make eval                                              # writes eval/results/latest.md
uv run --env-file .env milewise eval --model gpt-6.1-sol  # A/B
```

Gold answers are still awaiting human verification (`verified_at` in `eval/cases.yaml`).
