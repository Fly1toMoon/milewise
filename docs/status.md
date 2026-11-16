# 进度与待办

最后更新：2026-11-16（live v32：规则追问改由 Kimi K3.1 回答，仓库重建）。详细记录见 [design.md §9](design.md)。

## 现状

| 部分 | 状态 | 备注 |
|---|---|---|
| ANA 国际线价目表（结构化 + 计算） | ✅ | 558 条价格、3 套季节日历、混合季节规则 |
| JAL 国际线价目表（结构化 + 计算） | ✅ | 284 条价格（基本里程 = 下限 / 头等舱 L·R·H / PLUS 最大 = 上限）；按机场、航班号区分；往返 = 两段单程之和 |
| 合作航空价目表 | ✅ | ANA 提携航空会社特典：往返 183 + 单程 183 条（Zone 1-A/1-B、无季节，可按国家/地区查询，如"中国→欧洲"）。JAL ワンワールド（30 条）/ JMB 提携（52 条）：按总飞行距离分档；没给距离时用机场坐标（OurAirports）估算直飞大圆距离，靠近档位边界时给出两档 |
| 规则 RAG | ✅ | ANA 15 页 + JAL 21 页（共 36 页）。范围见 `data/sources.yaml`。构建时生成索引（`make index`）打进镜像，内存检索；向量召回 → Nova 2 Lite 重排 → 按页补全（design.md §4.13）；embedding 用 Cohere Multilingual v3（§9.18） |
| Agent / 后置校验 | ✅ | 取证 / 写 / 校验三段（design.md §4.13）：数字、引用、URL 都只允许来自本轮工具结果，不通过重写一次，仍不通过则在回答末尾提示。模型全部在 Bedrock |
| 评测 | 🟡 | 44 题。本地 2026-11-15：43/44（唯一失败的 miles-21 是裁判引用原句对不上的疑似误判，见 `eval/results/latest.md`）；云端发布闸门 2026-11-15：44/44（miles-16 重试后通过），$1.07。裁判 DeepSeek V3.2，逐条判定。**标准答案已由出题者复核，但还没有人工核对** |
| 云端 | ✅ | live v32（`aab447c`，2026-11-15 由 GitHub deploy 发布：索引 → 镜像 → candidate → 评测闸门 → promote）。Lambda + DynamoDB，无数据库；模型全部在 Bedrock |
| 公开访问 | ✅ | 网页不需要 token：按 IP 限流（10 分钟 5 次 / 每天 20 次）、回答缓存、全站每日 1000 次或 $5（先到先停，触发时发邮件）、每月 $50（到了就停到下月）、`make public-off` 开关；Cloudflare Turnstile 人机验证已启用，每次访问在后台验证一次、6 小时内有效；网址 `milewise-ai.pages.dev`（Cloudflare Pages 转发，design.md §4.8） |
| 回答模型 | ✅ | GLM-5（design.md §4.11）；上一条回答引用过规则条文的追问由 Kimi K3.1 回答（`follow_up_model`，§4.13）。GLM-5 约 $0.015/问、7 秒；Kimi 约 $0.1/问、20 秒 |
| 网页 | ✅ | 2026-10-19 支持多轮对话（design.md §4.10）：对话式布局、可追问、对话列表；之前同日重做（design.md §4.9）：航图风格、逐字流式回答（显示"正在查里程表/检索规则"）、可点击的引用与来源详情、本地历史记录、中/日/英界面、手机布局、深色模式 |
| Dashboard | ✅ | CloudWatch `milewise`：提问数/错误、延迟 p50/p95/冷启动、每日费用（对照上限）、token 与后置校验失败、最近 50 次提问 |
| CI（GitHub Actions） | ✅ | `ci.yml`（PR：lint / 类型 / 测试 / Terraform validate）、`deploy.yml`（main 的每次 push：带评测闸门的发布）。2026-11-15 仓库重建（去掉提交里的 AI 署名），OIDC 信任条件已指向新仓库 |

## 待办（按优先级）

1. ~~**检索时去重**~~ ✅ 2026-10-17（`3c0ab8a`）：导入时完整保存每一页，检索时按 `body_sha256` 合并重复 → 结果与导入顺序、并行度无关
2. ~~**JAL 价目表的结构化**~~ ✅ 2026-10-17（`c481ef9`）：路线制解析器 + JL 计算 + 9 个 golden 测试 + 3 道评测题
3. ~~**CI**~~ ✅ 2026-10-17：`.github/workflows/{ci,deploy}.yml`；`scripts/release.sh` 不依赖 Terraform state，只通过 AWS API 解析资源（deploy 角色保持最小权限）。PR 上的 `terraform plan` 推迟（只读角色读不到 secret 值，需要 `-refresh=false` 等方案）
4. ~~**合作航空价目表的结构化**~~ ✅ 2026-10-18（design.md §9.15）
5. ~~**CloudWatch Dashboard**~~ ✅ 2026-10-18（已 apply）
6. ~~**检索评测**~~ ✅ 2026-10-18：Titan V2 hit@5 70%（中文 33%）、OpenAI large 85%（design.md §9.16）
9. ~~**改善中文检索**~~ ✅ 2026-10-19：embedding 改为 Cohere Multilingual v3（hit@5 70% → 95%，中文 33% → 83%），规则按自社便 / 合作航空过滤（design.md §9.18）
11. rules-05（退票后里程有效期）仍不稳定：Nova 2 Lite 有时把"悪天候"情形的 36 个月规则当成一般规则；可考虑换更强的回答模型或在工具层标记特殊情形
12. 清理：删除旧表 `milewise-usage`；Lambda 角色里的 Titan embedding 权限已从 `infra/app/lambda.tf` 去掉，等下一次 `make app-apply` 生效
13. 中文提问时，回答里价目表的单位有时仍写成"マイル"（模型照抄工具结果），可在工具结果里按语言给出单位
14. ~~**多轮追问票规时不查资料、编规则、被质疑就附和**~~ ✅ 2026-11-15 上线（design.md §4.13，过程见 [findings/2026-10-22-multiturn-rules.md](findings/2026-10-22-multiturn-rules.md)、[findings/2026-11-11-multiturn-plan.md](findings/2026-11-11-multiturn-plan.md)）。保留下来的改动：逐条裁判（DeepSeek V3.2）、按页检索、行程事实由代码计算、歧义注释由系统附加、取证 / 写 / 校验三段、规则追问路由到 Kimi K3.1。试过又删掉的两层：强制先查（`ForceTool`）和模型核对（`check_model`）——同日实测 multi-05〜08 各 5 次，GLM-5 带两层 14/19、只留强制先查 8/15，Kimi K3.1 两层都不用 20/20。上线前评测：multi-05〜08 各 3 次 12/12；全量 44 题本地 43/44、云端 44/44。
10. ~~**JAL 合作航空的距离估算**~~ ✅ 2026-10-18（design.md §9.15）
7. ~~**去掉 Aurora，数据随镜像发布**~~ ✅ 2026-10-18 上线（design.md §4.6、§9.13）
8. ~~**模型改为 Bedrock**~~ ✅ 2026-10-18 上线（design.md §4.7、§9.14）

## 需要人做的事

- [ ] 人工核对评测标准答案（`verified_at`）。2026-10-18 已由出题者（AI）对照保存的官网原文逐条复核（`crosschecked_at`），全部一致；但这不等于第三方核对
- [x] ~~决定默认往返还是单程~~：默认单程（2026-10-18）
- [x] ~~确认 SNS 订阅邮件~~（2026-10-18 已确认）
- [x] ~~是否下载 OurAirports 机场坐标~~：已下载，只提交用到的 77 个机场
- [ ] Turnstile 小组件的 Hostnames 加上 `milewise-ai.pages.dev`（之前加的 `milewise.pages.dev` 可以删掉）
- [ ] 确认第一张出现 Cohere（"AWS Marketplace software usage"）的账单是否被 credit 抵扣
- [x] ~~apply `infra/app` 和 `infra/bootstrap`、启用 deploy 工作流~~（2026-11-15 完成，live v32）
- [ ] 核对 Kimi K3.1 的实际账单：`config.PRICES` 里 `global.moonshotai.kimi-k3.1` 用的是 Kimi K3 global 的报价（$3.00 / $0.30 / $15.00 每百万 token），评测和每日预算里的费用都按它算
- [x] ~~在 Cloudflare 创建 Turnstile 小组件~~：已创建并启用
- [x] ~~给云端单独建一个 OpenAI key~~：不再需要（模型都在 Bedrock；OpenAI 只用于本地 A/B）
- [x] push 到 GitHub 并设置 CI 用的 repo 变量

## 已知局限

- 不支持国内线、升舱特典（v1 范围外）
- JAL 页面由人工保存（站点屏蔽脚本访问），更新依赖人
- 不查询空位，不包含燃油附加费、税费的金额
- 规则文档更新后要发布一次才生效（`make upload-raw` → 手动运行 deploy 工作流），这是有意的：数据变更也要过评测
