# Eval run dedd81f3 (2026-11-15T07:58Z)

model `bedrock:zai.glm-5` (follow-ups to rule answers: `bedrock:global.moonshotai.kimi-k3.1`), judge `bedrock:deepseek.v3.2`, git `4d6008b`, 44 cases (44 not yet human-verified)

| metric | value | threshold |
|---|---|---|
| miles accuracy | 100% | 90% |
| rules accuracy | 100% | 80% |
| abstain accuracy | 100% | 80% |
| citation accuracy | 93% | 80% |
| post-check failures | 0% | |
| overall | 98% | |
| latency avg / p95 | 8150 / 18079 ms | |
| cost avg / total | $0.02363 / $1.0398 | |
| rewritten after the check | 2 | |

| answered by | questions | cost avg | latency avg |
|---|---|---|---|
| `bedrock:global.moonshotai.kimi-k3.1` | 4 | $0.09530 | 19454 ms |
| `bedrock:zai.glm-5` | 40 | $0.01647 | 7019 ms |

**PASSED**

| case | cat | lang | model | result | notes |
|---|---|---|---|---|---|
| miles-01 | miles | ja | zai.glm-5 | ✅ |  |
| miles-02 | miles | zh | zai.glm-5 | ✅ |  |
| miles-03 | miles | en | zai.glm-5 | ✅ |  |
| miles-04 | miles | ja | zai.glm-5 | ✅ |  |
| miles-05 | miles | ja | zai.glm-5 | ✅ |  |
| miles-06 | miles | en | zai.glm-5 | ✅ |  |
| miles-07 | miles | zh | zai.glm-5 | ✅ |  |
| miles-08 | miles | ja | zai.glm-5 | ✅ |  |
| miles-09 | miles | ja | zai.glm-5 | ✅ |  |
| miles-10 | miles | zh | zai.glm-5 | ✅ |  |
| miles-11 | miles | en | zai.glm-5 | ✅ |  |
| miles-12 | miles | zh | zai.glm-5 | ✅ |  |
| miles-13 | miles | ja | zai.glm-5 | ✅ |  |
| miles-14 | miles | ja | zai.glm-5 | ✅ |  |
| miles-15 | miles | zh | zai.glm-5 | ✅ |  |
| miles-16 | miles | zh | zai.glm-5 | ✅ |  |
| miles-17 | miles | ja | zai.glm-5 | ✅ |  |
| miles-18 | miles | ja | zai.glm-5 | ✅ |  |
| rules-01 | rules | ja | zai.glm-5 | ✅ |  |
| rules-02 | rules | zh | zai.glm-5 | ✅ |  |
| rules-03 | rules | ja | zai.glm-5 | ✅ |  |
| rules-04 | rules | en | zai.glm-5 | ✅ |  |
| rules-05 | rules | ja | zai.glm-5 | ✅ |  |
| rules-06 | rules | zh | zai.glm-5 | ✅ |  |
| rules-07 | rules | ja | zai.glm-5 | ✅ |  |
| rules-08 | rules | ja | zai.glm-5 | ✅ |  |
| rules-09 | rules | zh | zai.glm-5 | ✅ |  |
| rules-10 | rules | ja | zai.glm-5 | ✅ |  |
| both-01 | both | ja | zai.glm-5 | ✅ |  |
| both-02 | both | en | zai.glm-5 | ✅ |  |
| abstain-01 | abstain | ja | zai.glm-5 | ✅ |  |
| abstain-02 | abstain | zh | zai.glm-5 | ✅ |  |
| abstain-03 | abstain | ja | zai.glm-5 | ✅ |  |
| multi-01 | miles | zh | zai.glm-5 | ✅ |  |
| multi-02 | miles | ja | zai.glm-5 | ✅ |  |
| multi-03 | rules | en | zai.glm-5 | ✅ |  |
| multi-04 | miles | zh | zai.glm-5 | ✅ |  |
| miles-19 | miles | ja | zai.glm-5 | ✅ |  |
| miles-20 | miles | en | zai.glm-5 | ✅ |  |
| miles-21 | miles | zh | zai.glm-5 | ❌ | The answer correctly states that the total distance is calculated by summing 7 flight segments, excluding the KUL-SIN land portion (M1), and that the total dist |
| multi-05 | rules | zh | global.moonshotai.kimi-k3.1 | ✅ |  |
| multi-06 | rules | zh | global.moonshotai.kimi-k3.1 | ✅ |  |
| multi-07 | rules | zh | global.moonshotai.kimi-k3.1 | ✅ |  |
| multi-08 | rules | zh | global.moonshotai.kimi-k3.1 | ✅ |  |
