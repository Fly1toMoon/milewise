# Extra permissions for the GitHub deploy role (created in bootstrap) that need ARNs
# from this stack. Removed with the stack, which is fine: deploys skip when it is down.
#   * build the rules index: read raw pages, reuse/store the previous index (design.md §4.6)
#   * evaluate the candidate: app token for its URL, OpenAI key for the judge,
#     and the eval history in the artifacts bucket (s3://…/eval/, granted in bootstrap)
locals {
  raw_bucket_arn       = "arn:aws:s3:::${local.bootstrap.raw_docs_bucket}"
  artifacts_bucket_arn = "arn:aws:s3:::${local.bootstrap.artifacts_bucket}"
}

data "aws_iam_policy_document" "deploy_eval" {
  statement {
    sid       = "Secrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app_token.arn, aws_secretsmanager_secret.openai.arn]
  }
  statement {
    sid       = "RawPagesList"
    actions   = ["s3:ListBucket"]
    resources = [local.raw_bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["nh/*", "jl/*"]
    }
  }
  statement {
    sid       = "RawPagesRead"
    actions   = ["s3:GetObject"]
    resources = ["${local.raw_bucket_arn}/nh/*", "${local.raw_bucket_arn}/jl/*"]
  }
  statement {
    sid       = "IndexCacheList"
    actions   = ["s3:ListBucket"]
    resources = [local.artifacts_bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["index/*", "eval/*"]
    }
  }
  statement {
    sid       = "IndexCacheReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${local.artifacts_bucket_arn}/index/*"]
  }
}

resource "aws_iam_role_policy" "deploy_eval" {
  name   = "app-stack-eval"
  role   = "milewise-github-deploy"
  policy = data.aws_iam_policy_document.deploy_eval.json
}
