# Eval run a26d1958 (2026-10-19T15:35Z)

model `bedrock:jp.amazon.nova-2-lite-v1:0`, judge `bedrock:apac.amazon.nova-pro-v1:0`, git `d2e3cdf-dirty`, 39 cases (39 not yet human-verified)

| metric | value | threshold |
|---|---|---|
| miles accuracy | 100% | 90% |
| rules accuracy | 100% | 80% |
| abstain accuracy | 100% | 80% |
| citation accuracy | 97% | 80% |
| post-check failures | 0% | |
| overall | 100% | |
| latency avg / p95 | 8210 / 13023 ms | |
| cost avg / total | $0.01029 / $0.4011 | |

**PASSED** · unstable: rules-05, abstain-01

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
| rules-05 | rules | ja | ✅ (unstable) | 1st try failed: The answer does not correctly state that the miles will return with the same validity period as at the time of exchange, and it incorrectly stat |
| rules-06 | rules | zh | ✅ |  |
| rules-07 | rules | ja | ✅ |  |
| rules-08 | rules | ja | ✅ |  |
| rules-09 | rules | zh | ✅ |  |
| rules-10 | rules | ja | ✅ |  |
| both-01 | both | ja | ✅ |  |
| both-02 | both | en | ✅ |  |
| abstain-01 | abstain | ja | ✅ (unstable) | 1st try failed: The assistant correctly declined to provide a specific miles figure for Delta Air Lines due to it being out of scope. However, the answer incorr |
| abstain-02 | abstain | zh | ✅ |  |
| abstain-03 | abstain | ja | ✅ |  |
| multi-01 | miles | zh | ✅ |  |
| multi-02 | miles | ja | ✅ |  |
| multi-03 | rules | en | ✅ |  |
| multi-04 | miles | zh | ✅ |  |
| miles-19 | miles | ja | ✅ |  |
| miles-20 | miles | en | ✅ |  |
