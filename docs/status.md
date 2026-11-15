# 进度与待办

最后更新：2026-10-19（网页公开访问上线：live v17）。详细记录见 [design.md §9](design.md)。

## 现状

| 部分 | 状态 | 备注 |
|---|---|---|
| ANA 国际线价目表（结构化 + 计算） | ✅ | 558 条价格、3 套季节日历、混合季节规则 |
| JAL 国际线价目表（结构化 + 计算） | ✅ | 284 条价格（基本里程 = 下限 / 头等舱 L·R·H / PLUS 最大 = 上限）；按机场、航班号区分；往返 = 两段单程之和 |
| 合作航空价目表 | ✅ | ANA 提携航空会社特典：往返 183 + 单程 183 条（Zone 1-A/1-B、无季节，可按国家/地区查询，如"中国→欧洲"）。JAL ワンワールド（30 条）/ JMB 提携（52 条）：按总飞行距离分档；没给距离时用机场坐标（OurAirports）估算直飞大圆距离，靠近档位边界时给出两档 |
| 规则 RAG | ✅ | ANA 15 页 + JAL 21 页（共 36 页，305 个 chunk）。范围见 `data/sources.yaml`。**2026-10-18 起**：构建时生成索引（`make index`）打进镜像，内存检索（每次 < 3 ms），重复的通知在检索时合并 |
| Agent / 后置校验 | ✅ | 数字、引用、URL 都只允许来自工具结果。**2026-10-18 起模型全部在 Bedrock**：问答 / 重排 Nova 2 Lite，裁判 Nova Pro，embedding Titan V2（design.md §4.7） |
| 评测 | 🟡 | 32 题全部通过（Nova 2 Lite，本地，2026-10-18，无不稳定题，$0.0093/问）：含伙伴航空 3 题、默认单程；检索评测另见 design.md §9.16。**标准答案已由出题者复核，但还没有人工核对** |
| 云端 | ✅ | live v13（`a4afb6a`，PR #3 合并后由 GitHub deploy 自动发布：索引从 S3 构建、embedding 全部命中缓存、candidate 评测 30/30、$0.28）。之前 v12（`201d5f1`）：Lambda + DynamoDB，无数据库；模型全部在 Bedrock。冒烟测试通过（7.1 s，$0.0063/问）。Aurora、SQS、导入 Lambda 已删除，评测历史导出到 `s3://…artifacts…/eval/aurora-export-2026-10-18/` |
| 公开访问 | ✅ | live v17（`f6885dd`）已上线并实测：不带 token 可以提问、同一问题第二次命中缓存（$0）、伪造 `x-amzn-request-context` / `X-Forwarded-For` 无法绕过按 IP 限流（第 6 次返回 `ip_window`）。网页不再需要 token：按 IP 限流（10 分钟 5 次 / 每天 20 次）、回答缓存、全站每日 1000 次或 $2（先到先停，触发时发邮件）、`make public-off` 开关；Turnstile 已支持，**等你在 Cloudflare 创建 site key 后启用**（design.md §4.8） |
| 回答模型 | ✅ | 2026-10-21 起 GLM-5（design.md §4.11），重排序仍用 Nova 2 Lite |
| 网页 | ✅ | 2026-10-19 支持多轮对话（design.md §4.10）：对话式布局、可追问、对话列表；之前同日重做（design.md §4.9）：航图风格、逐字流式回答（显示"正在查里程表/检索规则"）、可点击的引用与来源详情、本地历史记录、中/日/英界面、手机布局、深色模式 |
| Dashboard | ✅ | CloudWatch `milewise`：提问数/错误、延迟 p50/p95/冷启动、每日费用（对照上限）、token 与后置校验失败、最近 50 次提问 |
| CI（GitHub Actions） | ✅ | `ci.yml`（PR：lint / 类型 / 测试 + pgvector / Terraform validate）、`deploy.yml`（main：带评测闸门的发布）。PR #1 合并后首次 deploy：评测 25/25 通过，live → v9（`5d530c0`），评测费用 $0.016 |

## 待办（按优先级）

1. ~~**检索时去重**~~ ✅ 2026-10-17（`3c0ab8a`）：导入时完整保存每一页，检索时按 `body_sha256` 合并重复 → 结果与导入顺序、并行度无关
2. ~~**JAL 价目表的结构化**~~ ✅ 2026-10-17（`c481ef9`）：路线制解析器 + JL 计算 + 9 个 golden 测试 + 3 道评测题
3. ~~**CI**~~ ✅ 2026-10-17：`.github/workflows/{ci,deploy}.yml`；`scripts/release.sh` 不依赖 Terraform state，只通过 AWS API 解析资源（deploy 角色保持最小权限）。PR 上的 `terraform plan` 推迟（只读角色读不到 secret 值，需要 `-refresh=false` 等方案）
4. ~~**合作航空价目表的结构化**~~ ✅ 2026-10-18（design.md §9.15）
5. ~~**CloudWatch Dashboard**~~ ✅ 2026-10-18（已 apply）
6. ~~**检索评测**~~ ✅ 2026-10-18：Titan V2 hit@5 70%（中文 33%）、OpenAI large 85%（design.md §9.16）
9. ~~**改善中文检索**~~ ✅ 2026-10-19：embedding 改为 Cohere Multilingual v3（hit@5 70% → 95%，中文 33% → 83%），规则按自社便 / 合作航空过滤（design.md §9.18）
11. rules-05（退票后里程有效期）仍不稳定：Nova 2 Lite 有时把"悪天候"情形的 36 个月规则当成一般规则；可考虑换更强的回答模型或在工具层标记特殊情形
12. 清理：删除旧表 `milewise-usage`，IAM 里去掉 Titan embedding 权限
13. 中文提问时，回答里价目表的单位有时仍写成"マイル"（模型照抄工具结果），可在工具结果里按语言给出单位
14. 多轮追问票规时不查资料、编规则、被质疑就附和（2026-10-22，GLM-5）：原因与改进方向见 [findings/2026-10-22-multiturn-rules.md](findings/2026-10-22-multiturn-rules.md)。第一组（没查就重答、上文带规则原文和第一问、反驳/歧义时的提示词、评测 multi-05〜08）2026-11-11 已实现（design.md §4.12）；2026-11-11〜12 按 [findings/2026-11-11-multiturn-plan.md](findings/2026-11-11-multiturn-plan.md) 做了步骤 1〜5（design.md §4.13：逐条裁判 + DeepSeek V3.2 裁判、按页检索、行程事实、歧义注释、全角引用与提示词分段），分支 `fix/multiturn-rules-plan`。multi-05〜08（GLM-5，各 5 次）5/20 → 9/20（步骤 3 后）→ 10/19（步骤 1〜5 全部，1 次超时不计）；全量 44 题保持 40/44；multi-08（用户反驳）始终 0/5。待办：两条歧义注释的文字需用户确认（"出発国"注释会干扰 multi-06）；步骤 6（生成后核对）建议做；部署前把 `infra/app/variables.tf` 的 `judge_model` 改成 `deepseek.v3.2`；2026-11-12 用户同意后：（A）"出発国"注释只在行程同时有 CN 与 HK/MO/TW 时附上（`when_itinerary`），multi-06 1/5 → 4/5、multi-07 5/5，multi-05 4/8（未见与注释有关，但比上一行低）；（B）步骤 6：规则回答后的追问强制先调用 `search_rules`，回答后用 DeepSeek V3.2 核对、不通过重答一次，multi-08 0/5 → 4/5、multi-06 5/5，但 multi-07 5/5 → 2/5（核对漏判和误导重答），完成标准"其余三题不下降"未达到，待用户决定；全量 41/44。2026-11-15 把这些机制收敛成"取证 / 写 / 校验"三段（design.md §4.13），随后按用户决定改为：上一条回答引用过规则条文（`evidence` 里有索引能查到的 chunk id）的追问由 Kimi K3.1（`follow_up_model`）回答，其余仍用 GLM-5；强制先查（`ForceTool`）和模型核对（`check_model`）两层删除，校验只剩数字、引用、链接的确定性检查和一次重写，提示词第 13〜18 条恢复重构前原文的信息量。依据是同日实测 multi-05〜08 各 5 次：GLM-5 带两层 14/19、只留强制先查 8/15，Kimi K3.1 两层都不用 20/20。改完后评测（commit `4d6008b`，裁判 DeepSeek V3.2）：multi-05〜08 各 3 次共 12/12，全部由 Kimi 回答且每次都自己调用了 `search_rules`，平均每问约 $0.097（含裁判）、20 秒；全量 44 题一次（不重试）43/44，其中 4 题（multi-05〜08）走 Kimi、40 题走 GLM-5，唯一失败的 miles-21（GLM-5）回答里写了 7 段飞行和陆路不计入距离，是裁判引用原句对不上导致的疑似误判；确定性检查触发重写 2 次（miles-08、miles-16，重写后都通过）；两次评测共 $2.20。待办：两条歧义注释的文字仍需用户确认。
10. ~~**JAL 合作航空的距离估算**~~ ✅ 2026-10-18（design.md §9.15）
7. ~~**去掉 Aurora，数据随镜像发布**~~ ✅ 2026-10-18 上线（design.md §4.6、§9.13）
8. ~~**模型改为 Bedrock**~~ ✅ 2026-10-18 上线（design.md §4.7、§9.14）

## 需要人做的事

- [ ] 人工核对评测标准答案（`verified_at`）。2026-10-18 已由出题者（Claude）对照保存的官网原文逐条复核（`crosschecked_at`），全部一致；但这不等于第三方核对
- [x] ~~决定默认往返还是单程~~：默认单程（2026-10-18）
- [x] ~~确认 SNS 订阅邮件~~（2026-10-18 已确认）
- [x] ~~是否下载 OurAirports 机场坐标~~：已下载，只提交用到的 77 个机场
- [ ] 确认第一张出现 Cohere（"AWS Marketplace software usage"）的账单是否被 credit 抵扣
- [ ] 部署前：部署角色（CI 评测）和 Lambda 执行角色都要有调用 Bedrock `global.moonshotai.kimi-k3.1`（Kimi K3.1，global 跨区域推理配置文件）的权限，否则规则追问会 AccessDenied（`infra/app/lambda.tf` 的 `bedrock_chat` 现在只放行 `chat_model` 和 `rerank_model`；Lambda 环境变量没有 `FOLLOW_UP_MODEL`，用的是 `config.py` 的默认值）；同时把 `judge_model` 改成 `deepseek.v3.2`（见待办第 14 项）。这次没有改 infra，怎么改由你决定
- [ ] 核对 Kimi K3.1 的实际账单：`config.PRICES` 里 `global.moonshotai.kimi-k3.1` 用的是 Kimi K3 global 的报价（$3.00 / $0.30 / $15.00 每百万 token），评测和每日预算里的费用都按它算
- [ ] （可选）在 Cloudflare 创建 Turnstile 小组件，把 site key 告诉我、secret 放进 `.env` 的 `TURNSTILE_SECRET`，然后 `make put-turnstile-secret`
- [x] ~~给云端单独建一个 OpenAI key~~：不再需要（模型都在 Bedrock；OpenAI 只用于本地 A/B）
- [x] push 到 GitHub 并设置 CI 用的 repo 变量

## 已知局限

- 不支持国内线、升舱特典（v1 范围外）
- JAL 页面由人工保存（站点屏蔽脚本访问），更新依赖人
- 不查询空位，不包含燃油附加费、税费的金额
- 规则文档更新后要发布一次才生效（`make upload-raw` → 手动运行 deploy 工作流），这是有意的：数据变更也要过评测
