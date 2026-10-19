# Eval run 227da320 (2026-10-19T12:45Z)

model `bedrock:jp.amazon.nova-2-lite-v1:0`, judge `bedrock:apac.amazon.nova-pro-v1:0`, git `5d66996-dirty`, 3 cases (3 not yet human-verified)

| metric | value | threshold |
|---|---|---|
| miles accuracy | 100% | 90% |
| rules accuracy | 100% | 80% |
| abstain accuracy | — | 80% |
| citation accuracy | 100% | 80% |
| post-check failures | 0% | |
| overall | 100% | |
| latency avg / p95 | 8339 / 6916 ms | |
| cost avg / total | $0.00906 / $0.0272 | |

**PASSED**

| case | cat | lang | result | notes |
|---|---|---|---|---|
| multi-01 | miles | zh | ✅ |  |
| multi-02 | miles | ja | ✅ |  |
| multi-03 | rules | en | ✅ |  |
