# 准备工作（你这边要做的事）

> 更新于 2026-10-16 晚：
> - LLM 改为 OpenAI 官方 API（`gpt-6-luna`）；
> - AWS 改为新开的 **Free plan（6 个月）** 账户；
> - 重新检查了这台 Mac。

## 当前状态

| 项目 | 状态 |
|---|---|
| macOS 27.0.1 / Homebrew / Xcode CLT / jq | ✅ |
| aws-cli 2.38.1（支持 `aws login`）/ terraform 1.16.6 / uv 0.13.1 / gh 2.102 | ✅ 已装 |
| OrbStack（`docker` 29.8.2），状态 Running | ✅ |
| `make setup`（Python 3.12 + 依赖）| ✅ 已执行；我已按 OpenAI 依赖重新同步，测试和 lint 都通过 |
| AWS 本地凭证（`~/.aws` 不存在） | ❌ → 步骤 1 |
| OpenAI 项目和 API key（`.env` 不存在） | ❌ → 步骤 2 |
| GitHub repo / `terraform.tfvars` | ❌ → 步骤 3 |

**原来的"Bedrock 模型访问"一步已经取消。** 剩下 3 步，大约 30 分钟。做完告诉我，我来继续：`make check-openai` → bootstrap plan（给你看）→ apply → Aurora spike。

---

## 1. AWS Free plan 账户与本地凭证（约 15 分钟）

**1-1. 先确认账户状态**（控制台）

- root 用户开启 MFA。
- 打开 Billing and Cost Management → **Credits**，记下：
  - 抵扣额度余额（应该是 $100，做完官方的"探索任务"最多再加 $100）；
  - **到期日**（开户后 6 个月）。
- 把到期日告诉我，我会写进 README 和 design.md R21。**到期前 2 周要升级到 Paid plan**：不升级的话，账户会被暂停，90 天后连同数据一起删除。
- 确认东京（ap-northeast-1）可以使用：控制台右上角切换到東京后，能打开 Lambda 和 RDS 的页面即可。

**1-2. 不要做的事**（会让免费额度立即失效，或者无法撤销）

- ❌ 不要启用 "advanced features"；
- ❌ 不要创建或加入 AWS Organizations、Control Tower；
- IAM Identity Center 在 Free plan 下不可用，所以**本地凭证改用 `aws login`**（见 1-4）。

**1-3. 日常操作用的 IAM 用户**（不要日常用 root）

控制台 → IAM → Users → Create user：

1. 用户名填 `milewise-admin`；
2. 勾选 "Provide user access to the AWS Management Console"，设置密码；
3. 权限选 Attach policies directly → `AdministratorAccess`；
4. 创建后，给这个用户启用 MFA；
5. **不要创建 access key。**

**1-4. 本地登录**（用控制台凭证换取临时凭证，不需要长期 access key）

```bash
aws login --profile milewise --region ap-northeast-1
```

浏览器会打开，用 `milewise-admin` 登录即可。然后在 `~/.zprofile` 加上这两行，并重开终端：

```bash
export AWS_PROFILE=milewise
export AWS_REGION=ap-northeast-1
```

确认（应该显示 `...:user/milewise-admin`）：

```bash
make whoami
```

> 如果之后 terraform 或 boto3 读不到 `aws login` 的凭证（老版本 SDK 可能不支持），先执行下面这条，把临时凭证导出成环境变量，再继续：
>
> ```bash
> eval "$(aws configure export-credentials --profile milewise --format env)"
> ```

## 2. OpenAI 项目、API key 与费用上限（约 10 分钟）

在 platform.openai.com 操作：

1. **创建项目** `milewise`。之后的用量、预算和 key 都按这个项目划分。
2. **计费：充值预付额度，并关闭自动充值（auto-recharge）。** 先充 $10 就够用很久（预计每月约 $3）。余额就是硬上限：即使出现死循环或被人刷，最多也只会损失余额（design.md R20）。
3. 在项目设置里配置**月度预算和告警邮件**（例如预算 $10，80% 时提醒）。
4. **创建项目级 API key**，选 Restricted 权限，只开放模型调用（Responses）、Embeddings 和 Models 的读取。名字填 `milewise-local`。
   - 之后部署到 AWS 时会再建一个 `milewise-lambda`。本地和云端分开，任何一边泄露都可以单独作废。
5. 把 key 写进本地 `.env`：

   ```bash
   cp .env.example .env
   ```

   用编辑器打开 `.env`，填 `OPENAI_API_KEY=`。**`.env` 已在 `.gitignore` 里，绝对不要提交，也不要贴进聊天。** 我执行 `make check-openai` 时会自己读这个文件。

## 3. GitHub repo 与 bootstrap 变量（约 5 分钟）

1. 创建 GitHub repo（作品集建议 public）。先不要 push，第一次 commit 等你 review 完代码再做。

   ```bash
   gh auth login
   ```

   ```bash
   gh repo create milewise --public --description "Award-ticket assistant on AWS with the OpenAI API"
   ```

2. 复制变量文件：

   ```bash
   cp infra/bootstrap/terraform.tfvars.example infra/bootstrap/terraform.tfvars
   ```

3. 编辑 `infra/bootstrap/terraform.tfvars`：
   - `budget_email`：接收 AWS 预算告警的邮箱；
   - `github_repository`：`你的 GitHub 用户名/milewise`。

   这是新账户，GitHub OIDC provider 肯定还不存在，`create_github_oidc_provider` 保持默认的 true 即可。

---

## 之后由我执行的内容（供你参考）

| 步骤 | 命令 | 费用 / 时间 |
|---|---|---|
| OpenAI 检查 | `make check-openai`：模型可见性、Responses API 工具调用、structured outputs、embedding（large 和 small，1024 维，外加中日跨语言的简单检查） | < $0.001 |
| bootstrap | `make bootstrap-init && make bootstrap-plan` → **把 plan 给你确认** → `make bootstrap-apply` → `make bootstrap-backend`（把 state 迁移到 S3） | 从抵扣额度中扣，约 $0.1/月；2 分钟 |
| Aurora spike | `make spike-up` → `make spike-run` → `make spike-resume`（实测从暂停中唤醒的耗时） → `make spike-down` | 从抵扣额度中扣，< $0.5；约 30 分钟 |

每一次 `terraform apply` 之前，我都会先把 plan 的摘要给你看，你确认后我再执行。

**为什么 spike 仍然必要**：改用 OpenAI 之后，Lambda 依然要走公网访问 `api.openai.com`。所以"Lambda 不挂 VPC + 通过 Data API 访问 Aurora"这个关键前提（design.md R1）不变，而且这次还要顺便确认 Free plan 账户能不能用 RDS Data API。

---

## 附：手动保存页面的放置方法（2026-10-17 已完成）

JAL 屏蔽脚本访问，ANA 的部分页面也一样，所以这些页面由人用浏览器保存。

1. 保存到 `data/raw/<任意包名>/`，包内包含 `manifest.json`（`saved_at`，以及每页的 `id` / `file` / `url`）和 `html/<id>.html`。这个目录已被 gitignore，也不会进 Docker 镜像。
2. 运行：

   ```bash
   make import-raw
   ```

   它会转换成 `raw/<airline>/<id>_<时间戳>.html`。没有登记在 `data/sources.yaml` 里的页面会被报出来。
3. 本地导入：

   ```bash
   make ingest-local
   ```

   云端同步则用 `make deploy`，然后 `make upload-raw`。
