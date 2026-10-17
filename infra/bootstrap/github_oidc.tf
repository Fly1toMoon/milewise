# GitHub Actions -> AWS without long-lived keys.
# Two roles: a read-only "plan" role for pull requests and a narrowly scoped
# "deploy" role for main. Infrastructure apply stays local in v1 (design.md R16).

locals {
  github_oidc_url = "token.actions.githubusercontent.com"
  github_oidc_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
  tfstate_arn     = aws_s3_bucket.this["tfstate"].arn
  artifacts_arn   = aws_s3_bucket.this["artifacts"].arn
  lambda_arn_glob = "arn:aws:lambda:${var.region}:${local.account_id}:function:milewise-*"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://${local.github_oidc_url}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://${local.github_oidc_url}"
}

# ---------- plan role (pull requests) ----------
data "aws_iam_policy_document" "plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "${local.github_oidc_url}:sub"
      values = [
        "repo:${var.github_repository}:pull_request",
        "repo:${var.github_repository}:ref:refs/heads/*",
      ]
    }
  }
}

resource "aws_iam_role" "github_plan" {
  name                 = "milewise-github-plan"
  assume_role_policy   = data.aws_iam_policy_document.plan_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "github_plan_readonly" {
  role       = aws_iam_role.github_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "plan_state" {
  # Read state; CI plans run with -lock=false so no lockfile writes are needed.
  statement {
    actions   = ["s3:ListBucket"]
    resources = [local.tfstate_arn]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${local.tfstate_arn}/*"]
  }
  # ReadOnlyAccess includes secretsmanager:Describe* but not GetSecretValue; keep it that way.
}

resource "aws_iam_role_policy" "github_plan_state" {
  name   = "tfstate-read"
  role   = aws_iam_role.github_plan.id
  policy = data.aws_iam_policy_document.plan_state.json
}

# ---------- deploy role (main branch only) ----------
data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "milewise-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.app.arn]
  }
  statement {
    sid = "LambdaRelease"
    actions = [
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:UpdateFunctionCode",
      "lambda:PublishVersion",
      "lambda:GetAlias",
      "lambda:UpdateAlias",
      "lambda:GetFunctionUrlConfig",
    ]
    resources = [local.lambda_arn_glob, "${local.lambda_arn_glob}:*"]
  }
  statement {
    sid       = "DetectEnvironment"
    actions   = ["rds:DescribeDBClusters"]
    resources = ["*"]
  }
  # Data API + DB secret access for writing eval results is attached by the app
  # stack (M4), which knows the exact cluster and secret ARNs.
  statement {
    sid       = "EvalArtifacts"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${local.artifacts_arn}/eval/*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
