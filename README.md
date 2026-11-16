# Milewise

Award-ticket (mileage) assistant for **ANA Mileage Club** and **JAL Mileage Bank**, running on AWS Lambda with Amazon Bedrock (GLM-5 answers, Kimi K3.1 for follow-ups about rules, Nova 2 Lite reranking, Cohere multilingual embeddings). Award charts and the rules index ship inside the container image, so data changes go through the same eval gate as code.

- Required miles are **computed by code** from structured award charts (no RAG guessing on numbers): ANA and JAL own-metal charts, ANA's partner-airline zone chart and JAL's distance-based partner charts.
- Rules, change and refund conditions are answered with **retrieval + citations** to the airlines' official pages.
- Every answer states its evidence (chart version / source URL), and says "not sure" when evidence is missing.
- Each question goes through three steps (`ask()` in `src/milewise/agent.py`): **ground** (pick the model, look things up with tools), **write**, **verify** (every miles figure, citation and link must come from this turn's tool results; one rewrite if not). Whether an itinerary is allowed is never decided in code.

**Try it: <https://milewise-ai.pages.dev>** (Chinese / Japanese / English; per-IP limits and a daily site budget apply).

> Work in progress. Design: [docs/design.md](docs/design.md) (Chinese). Setup: [docs/prerequisites.md](docs/prerequisites.md).

## Repository layout

```
src/milewise/          application: api.py (FastAPI), agent.py (ask), ground.py (model routing, tools),
                       prompt.py, verify.py, evidence.py, rag/ (index, search), charts/, evals/
data/charts/           structured award charts (CSV + source URL + fetch date), read directly at runtime
data/sources.yaml      the official pages indexed for rules questions; raw pages are never committed
data/rule_notes.yaml   editor's notes on rule sentences the official text leaves open (ja / zh / en)
build/index/           rules index built from raw pages (gitignored, shipped in the image)
eval/                  evaluation cases and results
infra/bootstrap/       persistent Terraform stack: state bucket, budgets, ECR, buckets, GitHub OIDC
infra/app/             destroyable stack: Lambda + Function URLs, DynamoDB usage table, secrets, alarms
edge/                  Cloudflare Pages proxy: milewise-ai.pages.dev -> the Lambda Function URL
scripts/               crawlers, parsers, preflight checks
```

## Quick start (local)

```bash
make setup                 # Python 3.12 deps via uv
cp .env.example .env       # models run on Bedrock: aws login --profile milewise (OpenAI key only for A/B runs)
make test                  # unit tests: parsers, miles golden values, index, post-check
make import-raw index      # saved pages -> raw/ -> build/index (chunks + embeddings)
make ask Q="東京→ロンドンのビジネスクラス特典航空券は何マイル必要？"
```

## In the cloud

```
browser ─▶ milewise-ai.pages.dev (Cloudflare Pages Function, edge/)
              │  forwards everything; adds the visitor IP + a shared secret
              ▼
           Lambda Function URL ─▶ FastAPI (page + /ask, streamed) ─▶ Bedrock
                                     │
                                     └─ DynamoDB: per-IP limits, answer cache, site budget
```

- **Open to anyone, with a hard cost ceiling** (design.md §4.8): per-IP limits (5 per 10 min,
  20 per day), cached answers, a site budget ($5 a day, $50 a month), and a kill switch
  (`make public-off`). Cloudflare Turnstile runs once per visit, in the background; the pass
  is a 6-hour cookie bound to the visitor's IP.
- **Eval-gated releases** (`.github/workflows/deploy.yml`): a push to `main` builds the image
  (code + rules index), deploys it to a `candidate` alias, runs the eval set against it, and
  moves `live` only if it passes. Docs-only pushes don't release; a change that cannot affect
  answers (page, bot check, infra, tests) releases without the eval, judged against the
  commit `live` serves.

## How it evolved

The first version stored everything in Aurora PostgreSQL Serverless v2 with pgvector, reached
from Lambda through the RDS Data API (no VPC, no NAT). Measured at real size (305 chunks,
~840 chart rows) that was the wrong tool: a paused cluster took ~20 s to answer the first
question, an always-on one cost ~$55/month, and the ingest pipeline wrote straight into the
live database, so new documents bypassed the eval gate. The data now ships in the image and is
searched in memory (<3 ms per query); see design.md §4.6 for the trade-offs.

## Models

| role | model | why |
|---|---|---|
| answers | `bedrock:zai.glm-5` | default for every question |
| follow-ups to a rule answer | `bedrock:global.moonshotai.kimi-k3.1` | used when the previous answer cited rule excerpts; GLM-5 agreed with users or misread itineraries there (design.md §4.13) |
| reranking in `search_rules` | `bedrock:jp.amazon.nova-2-lite-v1:0` | |
| embeddings | `bedrock:cohere.embed-multilingual-v3` | questions in zh / en against Japanese pages |
| eval judge | `bedrock:deepseek.v3.2` | 70/70 on the judge self-check (Nova Pro 53/70) |

## Evaluation (2026-11-15)

44 cases (miles 24 / rules 15 / both 2 / abstain 3; ja, zh, en; 8 of them multi-turn), gold answers
taken from the airlines' official pages, not from Milewise's own tools, and checked against
those pages by hand (2026-11-16; `verified_at` / `verified_by` in `eval/cases.yaml`). Miles are checked in code.
Rules and abstain cases are graded by an LLM judge that rules on each required and each forbidden
point and must quote the sentence it relied on; pass or fail is then computed in code.

| run | result | notes |
|---|---|---|
| all 44 cases, once, no retries | 43/44 | the one failure (miles-21) is a judge false negative: the quoted sentence did not match the answer's wording |
| multi-05〜08 (rule follow-ups, user pushback), 3 runs each | 12/12 | all answered by Kimi K3.1 |

Cost per question: about $0.016 on GLM-5 (40 cases, 7 s on average) and $0.094 on Kimi K3.1
(4 cases, 20 s). How the multi-turn cases got there: `docs/findings/2026-10-22-multiturn-rules.md`.

```bash
make eval                                                        # writes eval/results/latest.md
uv run --env-file .env milewise eval --only multi-07 --repeat 5  # one case, five runs, no retries
uv run --env-file .env milewise eval --model openai:gpt-6-luna   # A/B: one model for every question
```

Gold answers are still awaiting human verification (`verified_at` in `eval/cases.yaml`).
