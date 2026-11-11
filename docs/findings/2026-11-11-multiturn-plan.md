# 多轮票规问答：修改计划（2026-11-11）

背景与逐轮问题见 [2026-10-22-multiturn-rules.md](2026-10-22-multiturn-rules.md)。本计划接在该文档"第一组"之后，替代其中的"第二组、第三组"排序。

目标：multi-05〜08 在 GLM-5 上稳定通过，且全量题不退化。每一步单独一个 commit，做完跑 `make test`、`make lint`。

## 依据（今天 7 次评测的原始回答，`eval/results/2026-11-11T13*.jsonl`）

- oneworld 条件页全文 1,564 字，被切成 7 块。「旅程について」（136 字）和「利用区間・途中降機について」（419 字）是两块，一次查询常只拿到其中一块。
- multi-07，GLM-5：`f35f1286` 拿到「出発国へ戻り…」判"不可以"；`135d120c` 没拿到，判"可以"，并写了"日本国内段只有 PVG→HND"（实为国际段）。
- MiniMax 在 multi-08 的 5 次 `search_rules` 都没传 `award_type`，默认 `"own"`，查的是自社便规则。
- 裁判 Nova Pro：Kimi 的 multi-06 回答写了"允许出发地与最终目的地位于不同城市、甚至不同国家 [D5]"，仍被判缺这一点；`98911f92` 的 multi-05 含 must_not_say 的内容却通过。
- 索引共 305 块、约 9.9 万字；JAL 合作航空全部规则页约 1.5 万字。

## 步骤 1：评测先能量出差别

先做这一步，后面每步才有可比的数字。

改动（`src/milewise/evals/grade.py`、`run.py`、`cli.py`、`eval/cases.yaml`）：

1. 裁判逐条判定。`JUDGE_SCHEMA` 改为对每条 must_mention、must_not_say 各返回一项：`{point, verdict: bool, quote}`，`quote` 是回答里的原句（没有则为空）。`passed` 由代码计算：must_mention 全为真且 must_not_say 全为假。must_mention 判为真但 `quote` 不是回答的子串时，按未满足处理。
2. 确定性检查，不经过裁判，用例里新增可选字段：
   - `expected.must_call: [search_rules]`：`res.tool_calls` 里必须有。
   - `expected.gold_sections: ["旅程について", ...]`：本轮证据里必须有这些小节的块（比对 `evidence[*].section`）。
   - `expected.must_not_match: ["^你说得对"]`：对回答做正则。
3. 重复运行。`milewise eval` 增加 `--repeat N`（默认 1）；N > 1 时每题跑 N 次，报告里写"通过次数/N"，不再做"失败重试一次"。
4. multi 用例的 history 带证据。给 multi-05〜08 的 assistant 轮加 `evidence: [chunk_id, ...]`（oneworld 条件页的块 id 从 `build/index/chunks.jsonl` 取），让"上文带规则原文"在评测里生效。块 id 随内容变化，加一个测试：用例里的 id 都能在索引里找到。
5. 裁判模型。`config.py` 的 `judge_model` 改成账号里可用的最强模型；先用 `scripts/judge_selfcheck.py` 的做法，拿上面两个已知误判（Kimi multi-06、`98911f92` multi-05）做回归样本，确认新裁判判对。

完成标准：同一份回答重复判 5 次结果一致；上面两个误判样本判对；`milewise eval --only multi-05 … --repeat 5` 输出每题通过率。记录改动前的基线（GLM-5，multi-05〜08 各 5 次）。

## 步骤 2：规则检索按页返回

改动（`src/milewise/rag/search.py`、`rag/index.py`、`agent.py`）：

1. `RulesIndex` 增加按 `url` 取同页全部块（按 `ord` 排序）的方法。
2. `search_rules()`：重排选出块之后，按页展开。命中页的全文不超过 `PAGE_EXPAND_CHARS`（先定 3,000 字）时，返回该页全部块；超过时返回命中块及其前后各一块。总量上限 `MAX_RESULT_CHARS`（先定 8,000 字），按重排顺序填充。
3. 每个块仍各有自己的证据 id 和 `section`，引用方式不变。「重要なお知らせ」这类只有导航文字的块在展开时跳过（判断：正文没有「。」）。
4. `RulesArgs.award_type` 默认值从 `"own"` 改为 `None`（不过滤），描述改为"用户问合作航空/oneworld/星盟时传 partner，问自社便时传 own，不确定就不传"。确认 `index.recall` 对 `None` 不过滤。
5. `agent.py` 里 `HISTORY_EXCERPTS = 6` 相应调大，或改为按字数限制。

完成标准：新增测试——查询只命中「旅程について」时，结果里也有「利用区間・途中降機について」。`eval/retrieval.yaml` 的 hit@5 不下降。multi-05〜08 的 `gold_sections` 检查 5/5 通过。记录每问费用变化。

## 步骤 3：行程事实随工具结果返回

改动（`src/milewise/tools/miles.py`、`locations.py`、`agent.py`）：

1. 新增 `itinerary_facts(runs)`，与 `estimate_itinerary` 用同样的输入，返回：
   - `flown_segments`：飞行段数；每段的起止机场和国家代码（`airports.csv` 的 `iso_country`）。
   - `ground_transfers`：地面移动的位置和次数。
   - `japan_domestic_segments`：两端都在 JP 的段数。
   - `countries_in_order`、`origin_country`、`final_country`。
   - `returns_to_origin_country`：第几段回到出发国，之后又去了哪里；没有则为空。
   - `city_visits`：每个城市出现的次数。
   - `notes`：行程里同时出现 CN 与 HK / MO / TW 时，写明"代码不同，官方条件页没有定义它们是否算同一个国"。
2. 只数数，不判断合法与否。
3. `_jl_partner` 在有 `itinerary` 时把它放进结果的 `itinerary_facts`。
4. 用户只问"这样可以吗"、没问里程时，模型也要能拿到这些事实：`MilesArgs.cabin` 现在必填，规则类追问时模型会随便填一个舱位。给 `get_required_miles` 的描述和提示词第 4 条加一句"判断多段行程是否符合条件时，先带 itinerary 调用本工具拿 itinerary_facts，不要自己数段数、停留和国家"；舱位未知时沿用上文，上文没有就用 Y 并且不报里程。
5. 提示词加一条：段数、地面移动、日本国内区间、国家顺序只能引用 `itinerary_facts`。

完成标准：单元测试覆盖帖子里的票（7 段、地面移动 1 处、日本国内 0 段、出发 PH、终点 JP）和 multi-07 的行程（4 段、日本国内 0 段、第 3 段到 CN、出发 HK、`notes` 非空）。multi-07 通过率明显高于基线。

## 步骤 4：已知歧义做成可引用的注释

改动（新文件 `data/rule_notes.yaml`、`rag/index.py` 或 `rag/search.py`、`agent.py`、`api.py` 的 `evidence_card`、`static/index.html`）：

1. `data/rule_notes.yaml`，每条：`url`、`section`、`match`（条文里的原句）、`note`、`checked_at`。先写两条：
   - 「日本発着の旅程の場合、日本国内の途中降機はできません」：原文没有定义是否包括只到达日本的行程；已知实例：马尼拉出发、东京到达、大阪停留的票已出票（网帖，2026-10）。
   - 「出発国へ戻り、さらに第三国へ行く旅程は組めません」：原文没有定义香港、澳门、台湾与中国大陆是否同一出发国。
2. `search_rules` 返回的块如果命中某条注释（`url` + `section` + `match` 是块文本的子串），在该块上加 `editor_note` 字段。`match` 找不到时构建索引要报错，防止页面更新后注释悬空。
3. 提示词第 11 条改为：块带 `editor_note` 时必须转述，并说明这是 Milewise 的注释、不是官方条文；仍保留"摘录没回答的就说不确定"。
4. 网页的来源详情里显示注释，与官方原文分开。

完成标准：multi-05、06、08 的回答里出现"原文未定义"的通过率高于步骤 3 之后的数字；测试覆盖注释匹配和悬空报错。

注释内容是人工结论，需要用户确认后再合并。

## 步骤 5：小修

- `agent.py`：`_EVIDENCE_MARK`、`_CITE_MARK` 和 `grade.py` 的 `_MARK` 匹配前把全角【】、［］归一成 []；`index.html` 的引用解析同样处理。
- `SYSTEM_PROMPT` 重新编号并分成三段：通用、里程、规则与多轮。不改内容，只改顺序和编号，单独一个 commit，前后各跑一次全量评测确认没有退化。

## 步骤 6：视结果再定

步骤 1〜5 之后跑全量 44 题（重复 3 次）和 multi-05〜08（重复 5 次）。

- multi-08（用户反驳）仍低于 4/5：加一步核对。回答生成后，用 `llm.structured` 把回答、本轮摘录、上文第一问交给模型，逐句检查规则陈述是否有摘录支持、是否与上文的已出票行程冲突；不通过则带着检查结果重答一次。只在本轮调用过 `search_rules` 且有 history 时触发。
- 仍不够：有 history 且调用了 `search_rules` 的问题改用 Kimi K3.1，其余仍用 GLM-5。需要先确认 Kimi K3.1 的实际价格。

## 不做的事

- 不把规则写进代码做合法性判断（之前已否决）。
- 不改 embedding、不改分块大小（按页返回已解决粒度问题）。
- 不动部署；全部在本地评测通过后再走正常的发布流程。

## 文档

每步完成后更新 `docs/design.md`（新增 §4.13）和 `docs/status.md` 第 14 项，在 2026-10-22 的 findings 末尾补评测数字。
