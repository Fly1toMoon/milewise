# Eval run 6a4abcf5 (2026-10-19T06:59Z)

model `deployed`, judge `bedrock:apac.amazon.nova-pro-v1:0`, git `840be1d-dirty`, 33 cases (33 not yet human-verified)

| metric | value | threshold |
|---|---|---|
| miles accuracy | 100% | 90% |
| rules accuracy | 92% | 80% |
| abstain accuracy | 100% | 80% |
| citation accuracy | 100% | 80% |
| post-check failures | 3% | |
| overall | 97% | |
| latency avg / p95 | 7222 / 10545 ms | |
| cost avg / total | $0.00942 / $0.3109 | |

**PASSED** · unstable: miles-02

| case | cat | lang | result | notes |
|---|---|---|---|---|
| miles-01 | miles | ja | ✅ |  |
| miles-02 | miles | zh | ✅ (unstable) | 1st try failed: figures missing or unbacked;  |
| miles-03 | miles | en | ✅ |  |
| miles-04 | miles | ja | ✅ |  |
| miles-05 | miles | ja | ✅ |  |
| miles-06 | miles | en | ✅ |  |
| miles-07 | miles | zh | ✅ |  |
| miles-08 | miles | ja | ✅ |  |
| miles-09 | miles | ja | ✅ |  |
| miles-10 | miles | zh | ✅ |  |
| miles-11 | miles | en | ✅ |  |
| miles-12 | miles | zh | ✅ |  |
| miles-13 | miles | ja | ✅ |  |
| miles-14 | miles | ja | ✅ |  |
| miles-15 | miles | zh | ✅ |  |
| miles-16 | miles | zh | ✅ |  |
| miles-17 | miles | ja | ✅ |  |
| miles-18 | miles | ja | ✅ |  |
| rules-01 | rules | ja | ✅ |  |
| rules-02 | rules | zh | ✅ |  |
| rules-03 | rules | ja | ✅ |  |
| rules-04 | rules | en | ✅ |  |
| rules-05 | rules | ja | ❌ | The answer incorrectly states that the validity period of miles is set to 36 months from the refund date, which contradicts the required points. It also does no |
| rules-06 | rules | zh | ✅ |  |
| rules-07 | rules | ja | ✅ |  |
| rules-08 | rules | ja | ✅ |  |
| rules-09 | rules | zh | ✅ |  |
| rules-10 | rules | ja | ✅ |  |
| both-01 | both | ja | ✅ |  |
| both-02 | both | en | ✅ |  |
| abstain-01 | abstain | ja | ✅ |  |
| abstain-02 | abstain | zh | ✅ |  |
| abstain-03 | abstain | ja | ✅ |  |
