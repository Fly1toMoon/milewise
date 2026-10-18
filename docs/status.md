# 进度与待办

最后更新：2026-10-18（去掉 Aurora、模型改为 Bedrock：代码完成，云端还没 apply）。详细记录见 [design.md §9](design.md)。

## 现状

| 部分 | 状态 | 备注 |
|---|---|---|
| ANA 国际线价目表（结构化 + 计算） | ✅ | 558 条价格、3 套季节日历、混合季节规则 |
| JAL 国际线价目表（结构化 + 计算） | ✅ | 284 条价格（基本里程 = 下限 / 头等舱 L·R·H / PLUS 最大 = 上限）；按机场、航班号区分；往返 = 两段单程之和 |
| 规则 RAG | ✅ | ANA 15 页 + JAL 21 页（共 36 页，305 个 chunk）。范围见 `data/sources.yaml`。**2026-10-18 起**：构建时生成索引（`make index`）打进镜像，内存检索（每次 < 3 ms），重复的通知在检索时合并 |
| Agent / 后置校验 | ✅ | 数字、引用、URL 都只允许来自工具结果。**2026-10-18 起模型全部在 Bedrock**：问答 / 重排 Nova 2 Lite，裁判 Nova Pro，embedding Titan V2（design.md §4.7） |
| 评测 | 🟡 | 30 题全部通过（Nova 2 Lite，本地，2026-10-18；对照 Luna 97%，见 design.md §9.14）；**标准答案还没有人工核对** |
| 云端 | 🟡 | 线上（v10）仍是 Aurora 版。新版（Lambda + DynamoDB，无数据库）代码和 Terraform 已完成，**待 apply**：会删除 Aurora、SQS、导入 Lambda |
| CI（GitHub Actions） | ✅ | `ci.yml`（PR：lint / 类型 / 测试 + pgvector / Terraform validate）、`deploy.yml`（main：带评测闸门的发布）。PR #1 合并后首次 deploy：评测 25/25 通过，live → v9（`de6bc95`），评测费用 $0.016 |

## 待办（按优先级）

1. ~~**检索时去重**~~ ✅ 2026-10-17（`13bebfb`）：导入时完整保存每一页，检索时按 `body_sha256` 合并重复 → 结果与导入顺序、并行度无关
2. ~~**JAL 价目表的结构化**~~ ✅ 2026-10-17（`a2278b6`）：路线制解析器 + JL 计算 + 9 个 golden 测试 + 3 道评测题
3. ~~**CI**~~ ✅ 2026-10-17：`.github/workflows/{ci,deploy}.yml`；`scripts/release.sh` 不依赖 Terraform state，只通过 AWS API 解析资源（deploy 角色保持最小权限）。PR 上的 `terraform plan` 推迟（只读角色读不到 secret 值，需要 `-refresh=false` 等方案）
4. 合作航空价目表的结构化（ANA 提携、JAL ワンワールド / JMB 提携；有 PDF 和页面）
5. CloudWatch Dashboard
6. 检索评测（embedding large vs small）
7. ~~**去掉 Aurora，数据随镜像发布**~~ 🟡 2026-10-18 代码完成（design.md §4.6、§9.13），待云端 apply
8. ~~**模型改为 Bedrock**~~ 🟡 2026-10-18 代码完成并本地 A/B（design.md §4.7、§9.14），随 7 一起 apply

## 需要人做的事

- [ ] 核对评测标准答案（`eval/cases.yaml` 的 `verified_at`），包括新增的 miles-12～15、abstain-04
- [ ] 决定：没说往返/单程时默认按哪种回答（现在是往返；miles-12 按往返写）
- [ ] 确认 AWS 发来的 SNS 订阅邮件（否则告警邮件发不到）
- [x] ~~给云端单独建一个 OpenAI key~~：不再需要（模型都在 Bedrock；OpenAI 只用于本地 A/B）
- [x] push 到 GitHub 并设置 CI 用的 repo 变量

## 已知局限

- 不支持国内线、升舱特典（v1 范围外）
- JAL 页面由人工保存（站点屏蔽脚本访问），更新依赖人
- 不查询空位，不包含燃油附加费、税费的金额
- 数据库从暂停状态唤醒时，第一个问题要多等约 20 秒（新版 apply 后消失）
- 规则文档更新后要发布一次才生效（`make upload-raw` → 手动运行 deploy 工作流），这是有意的：数据变更也要过评测
