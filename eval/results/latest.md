# Eval run 7cc7fa7f (2026-11-11T13:12Z)

model `bedrock:global.moonshotai.kimi-k3.1`, judge `bedrock:apac.amazon.nova-pro-v1:0`, git `7b3bb05-dirty`, 18 cases (18 not yet human-verified)

| metric | value | threshold |
|---|---|---|
| miles accuracy | 100% | 90% |
| rules accuracy | 87% | 80% |
| abstain accuracy | — | 80% |
| citation accuracy | 100% | 80% |
| post-check failures | 0% | |
| overall | 89% | |
| latency avg / p95 | 28976 / 53619 ms | |
| cost avg / total | $0.07843 / $1.4117 | |

**PASSED**

| case | cat | lang | result | notes |
|---|---|---|---|---|
| rules-01 | rules | ja | ✅ |  |
| rules-02 | rules | zh | ✅ |  |
| rules-03 | rules | ja | ✅ |  |
| rules-04 | rules | en | ✅ |  |
| rules-05 | rules | ja | ✅ |  |
| rules-06 | rules | zh | ✅ |  |
| rules-07 | rules | ja | ✅ |  |
| rules-08 | rules | ja | ✅ |  |
| rules-09 | rules | zh | ✅ |  |
| rules-10 | rules | ja | ✅ |  |
| multi-01 | miles | zh | ✅ |  |
| multi-02 | miles | ja | ✅ |  |
| multi-03 | rules | en | ✅ |  |
| multi-04 | miles | zh | ✅ |  |
| multi-05 | rules | zh | ✅ |  |
| multi-06 | rules | zh | ❌ | The answer does not explicitly state that the final destination and departure city can be in different countries, even though it implies it. It also does not cl |
| multi-07 | rules | zh | ❌ | The answer does not explicitly mention the required point about the ambiguity of whether Hong Kong and mainland China are considered the same '出発国' and the need |
| multi-08 | rules | zh | ✅ |  |
