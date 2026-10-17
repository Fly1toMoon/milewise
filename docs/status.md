# 进度与待办

最后更新：2026-10-17（GitHub 上的 deploy 首次跑通）。详细记录见 [design.md §9](design.md)。

## 现状

| 部分 | 状态 | 备注 |
|---|---|---|
| ANA 国际线价目表（结构化 + 计算） | ✅ | 558 条价格、3 套季节日历、混合季节规则 |
| JAL 国际线价目表（结构化 + 计算） | ✅ | 284 条价格（基本里程 = 下限 / 头等舱 L·R·H / PLUS 最大 = 上限）；按机场、航班号区分；往返 = 两段单程之和 |
| 规则 RAG | ✅ | ANA 15 页 + JAL 21 页（共 36 页，305 个 chunk）。范围见 `data/sources.yaml`。重复的通知在检索时合并（云端与本地完全一致） |
| Agent / 后置校验 | ✅ | 数字、引用、URL 都只允许来自工具结果 |
| 评测 | 🟡 | 25 题全部通过（含 JAL 价格 3 题、JAL 规则 2 题）；**标准答案还没有人工核对** |
| 云端（Lambda / Aurora / 导入流水线） | ✅ | 线上可用。`make release` = candidate → 对 candidate URL 跑评测 → 通过才切到 live |
| CI（GitHub Actions） | ✅ | `ci.yml`（PR：lint / 类型 / 测试 + pgvector / Terraform validate）、`deploy.yml`（main：带评测闸门的发布）。PR #1 合并后首次 deploy：评测 25/25 通过，live → v9（`de6bc95`），评测费用 $0.016 |

## 待办（按优先级）

1. ~~**检索时去重**~~ ✅ 2026-10-17（`13bebfb`）：导入时完整保存每一页，检索时按 `body_sha256` 合并重复 → 结果与导入顺序、并行度无关
2. ~~**JAL 价目表的结构化**~~ ✅ 2026-10-17（`a2278b6`）：路线制解析器 + JL 计算 + 9 个 golden 测试 + 3 道评测题
3. ~~**CI**~~ ✅ 2026-10-17：`.github/workflows/{ci,deploy}.yml`；`scripts/release.sh` 不依赖 Terraform state，只通过 AWS API 解析资源（deploy 角色保持最小权限）。PR 上的 `terraform plan` 推迟（只读角色读不到 secret 值，需要 `-refresh=false` 等方案）
4. 合作航空价目表的结构化（ANA 提携、JAL ワンワールド / JMB 提携；有 PDF 和页面）
5. CloudWatch Dashboard
6. 检索评测（embedding large vs small）

## 需要人做的事

- [ ] 核对评测标准答案（`eval/cases.yaml` 的 `verified_at`）
- [ ] 确认 AWS 发来的 SNS 订阅邮件（否则告警邮件发不到）
- [ ] 给云端单独建一个 OpenAI key（`OPENAI_LAMBDA_API_KEY` → `make put-openai-key`）
- [x] push 到 GitHub 并设置 CI 用的 repo 变量

## 已知局限

- 不支持国内线、升舱特典（v1 范围外）
- JAL 页面由人工保存（站点屏蔽脚本访问），更新依赖人
- 不查询空位，不包含燃油附加费、税费的金额
- 数据库从暂停状态唤醒时，第一个问题要多等约 20 秒
