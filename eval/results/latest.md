# Eval run ec43347d (2026-10-19T06:11Z)

model `deployed`, judge `bedrock:apac.amazon.nova-pro-v1:0`, git `1ba8baa-dirty`, 32 cases (32 not yet human-verified)

| metric | value | threshold |
|---|---|---|
| miles accuracy | 100% | 90% |
| rules accuracy | 100% | 80% |
| abstain accuracy | 100% | 80% |
| citation accuracy | 100% | 80% |
| post-check failures | 0% | |
| overall | 100% | |
| latency avg / p95 | 8336 / 15671 ms | |
| cost avg / total | $0.01039 / $0.3323 | |

**PASSED** · unstable: miles-12, miles-15, rules-05, both-01

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
| miles-12 | miles | zh | ✅ (unstable) | 1st try failed: The answer incorrectly references 2026 instead of 2027, and incorrectly splits the dates for the regular season. / missing: ['指的是2027年的7月和8月'] / |
| miles-13 | miles | ja | ✅ |  |
| miles-14 | miles | ja | ✅ |  |
| miles-15 | miles | zh | ✅ (unstable) | 1st try failed: figures missing or unbacked; The answer correctly states the required points for both ANA and JAL, including the seasonal variations for ANA and |
| miles-16 | miles | zh | ✅ |  |
| miles-17 | miles | ja | ✅ |  |
| miles-18 | miles | ja | ✅ |  |
| rules-01 | rules | ja | ✅ |  |
| rules-02 | rules | zh | ✅ |  |
| rules-03 | rules | ja | ✅ |  |
| rules-04 | rules | en | ✅ |  |
| rules-05 | rules | ja | ✅ (unstable) | 1st try failed: The answer includes points not required by the rubric and does not fully address the required points clearly. / missing: ['通常の払い戻しは交換時と同じ有効期限で戻る |
| rules-06 | rules | zh | ✅ |  |
| rules-07 | rules | ja | ✅ |  |
| rules-08 | rules | ja | ✅ |  |
| rules-09 | rules | zh | ✅ |  |
| both-01 | both | ja | ✅ (unstable) | 1st try failed: figures missing or unbacked; The answer correctly states that miles are refundable if all segments are unused, which meets the required point. T |
| both-02 | both | en | ✅ |  |
| abstain-01 | abstain | ja | ✅ |  |
| abstain-02 | abstain | zh | ✅ |  |
| abstain-03 | abstain | ja | ✅ |  |
