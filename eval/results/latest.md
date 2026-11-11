# Eval run f35f1286 (2026-11-11T13:02Z)

model `bedrock:zai.glm-5`, judge `bedrock:apac.amazon.nova-pro-v1:0`, git `f70850e-dirty`, 44 cases (44 not yet human-verified)

| metric | value | threshold |
|---|---|---|
| miles accuracy | 100% | 90% |
| rules accuracy | 82% | 80% |
| abstain accuracy | 100% | 80% |
| citation accuracy | 93% | 80% |
| post-check failures | 9% | |
| overall | 93% | |
| latency avg / p95 | 9493 / 17111 ms | |
| cost avg / total | $0.01480 / $0.6512 | |

**PASSED** · unstable: miles-14, multi-04, multi-05

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
| miles-14 | miles | ja | ✅ (unstable) | 1st try failed: figures missing or unbacked; The answer correctly states the required points without any forbidden statements. / post-check failed |
| miles-15 | miles | zh | ✅ |  |
| miles-16 | miles | zh | ✅ |  |
| miles-17 | miles | ja | ✅ |  |
| miles-18 | miles | ja | ✅ |  |
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
| both-01 | both | ja | ✅ |  |
| both-02 | both | en | ✅ |  |
| abstain-01 | abstain | ja | ✅ |  |
| abstain-02 | abstain | zh | ✅ |  |
| abstain-03 | abstain | ja | ✅ |  |
| multi-01 | miles | zh | ✅ |  |
| multi-02 | miles | ja | ✅ |  |
| multi-03 | rules | en | ✅ |  |
| multi-04 | miles | zh | ✅ (unstable) | 1st try failed: figures missing or unbacked; The answer does not correctly state the total distance and the correct mileage required for the given route. The re |
| miles-19 | miles | ja | ✅ |  |
| miles-20 | miles | en | ✅ |  |
| miles-21 | miles | zh | ✅ |  |
| multi-05 | rules | zh | ✅ (unstable) | 1st try failed: The answer fails to explicitly state the required points regarding the limitations on stopovers in Japan when departing from China and the prohi |
| multi-06 | rules | zh | ❌ | The answer states that the proposed itinerary is not allowed due to the rule about stopping in Japan twice, which is not explicitly forbidden by the rubric. The |
| multi-07 | rules | zh | ❌ | The answer fails to mention the ambiguity regarding whether Hong Kong and mainland China are considered the same 'departure country', which is a crucial point i |
| multi-08 | rules | zh | ❌ | The answer directly confirms the user's statement, which is against the rubric's forbidden statements. It also fails to mention the ambiguity regarding whether  |
