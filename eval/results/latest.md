# Eval run 5100ea4a (2026-10-19T06:57Z)

model `bedrock:jp.amazon.nova-2-lite-v1:0`, judge `bedrock:apac.amazon.nova-pro-v1:0`, git `24d078c-dirty`, 33 cases (33 not yet human-verified)

| metric | value | threshold |
|---|---|---|
| miles accuracy | 100% | 90% |
| rules accuracy | 100% | 80% |
| abstain accuracy | 100% | 80% |
| citation accuracy | 100% | 80% |
| post-check failures | 0% | |
| overall | 100% | |
| latency avg / p95 | 7040 / 10646 ms | |
| cost avg / total | $0.00933 / $0.3079 | |

**PASSED** · unstable: rules-05

| case | cat | lang | result | notes |
|---|---|---|---|---|
| miles-01 | miles | ja | ✅ |  |
| miles-02 | miles | zh | ✅ |  |
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
| rules-05 | rules | ja | ✅ (unstable) | 1st try failed: The answer incorrectly states that the miles have a 36-month validity period from the refund date, which contradicts the required point that the |
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
