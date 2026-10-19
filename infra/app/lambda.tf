# The api function runs the image (FastAPI + Lambda Web Adapter) outside any VPC. Charts
# and the rules index are inside the image (design.md §4.6); models are on Amazon Bedrock
# (§4.7), reached over its public endpoint with IAM, so no NAT is involved.

locals {
  image_uri  = "${local.bootstrap.ecr_repository_url}:${var.image_tag}"
  account_id = data.aws_caller_identity.current.account_id
  common_env = {
    QUOTA_TABLE  = aws_dynamodb_table.quota.name
    CHAT_MODEL   = "bedrock:${var.chat_model}"
    RERANK_MODEL = "bedrock:${var.chat_model}"
    # Only read when a model is switched to openai:* for an A/B run (milewise.api).
    OPENAI_API_KEY_SECRET_ARN = aws_secretsmanager_secret.openai.arn
  }
  # A geo inference profile (jp. / apac.) routes to the foundation model in any of its
  # regions, so the model ARN is allowed in every region and the profile in this one.
  bedrock_chat = [
    "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${var.chat_model}",
    "arn:aws:bedrock:*::foundation-model/${replace(var.chat_model, "/^(jp|apac|global)\\./", "")}",
  ]
  bedrock_judge = [
    "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${var.judge_model}",
    "arn:aws:bedrock:*::foundation-model/${replace(var.judge_model, "/^(jp|apac|global)\\./", "")}",
  ]
  bedrock_embedding = ["arn:aws:bedrock:${var.region}::foundation-model/${var.embedding_model}"]
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "app_common" {
  statement {
    sid       = "Bedrock"
    actions   = ["bedrock:InvokeModel"]
    resources = concat(local.bedrock_chat, local.bedrock_embedding)
  }
  statement {
    sid       = "Quota"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.quota.arn, aws_dynamodb_table.usage.arn]
  }
  statement {
    sid       = "PublicBudgetAlert"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
  statement {
    sid     = "Secrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.openai.arn,
      aws_secretsmanager_secret.app_token.arn,
      aws_secretsmanager_secret.turnstile.arn,
    ]
  }
  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

# ---------- api ----------
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.name}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "api" {
  name               = "${local.name}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "api_logs" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "app"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.app_common.json
}

resource "aws_iam_role_policy" "api_logs" {
  name   = "logs"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api_logs.json
}

resource "aws_lambda_function" "api" {
  function_name = "${local.name}-api"
  role          = aws_iam_role.api.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["arm64"]
  memory_size   = 1024
  timeout       = 120
  publish       = true

  environment {
    variables = merge(local.common_env, {
      APP_TOKEN_SECRET_ARN        = aws_secretsmanager_secret.app_token.arn
      DAILY_REQUEST_LIMIT         = tostring(var.daily_request_limit) # owner pool
      DAILY_COST_LIMIT_USD        = tostring(var.daily_cost_limit_usd)
      PUBLIC_DAILY_REQUEST_LIMIT  = tostring(var.public_daily_request_limit)
      PUBLIC_DAILY_COST_LIMIT_USD = tostring(var.public_daily_cost_limit_usd)
      IP_WINDOW_LIMIT             = tostring(var.ip_window_limit)
      IP_DAILY_LIMIT              = tostring(var.ip_daily_limit)
      ALERT_TOPIC_ARN             = aws_sns_topic.alerts.arn
      TURNSTILE_SITE_KEY          = var.turnstile_site_key
      TURNSTILE_SECRET_ARN        = aws_secretsmanager_secret.turnstile.arn
    })
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format = "Text" # the app already writes one JSON/EMF line per request
    log_group  = aws_cloudwatch_log_group.api.name
  }

  lifecycle {
    ignore_changes = [image_uri] # CI deploys new code; Terraform owns configuration
  }

  depends_on = [aws_iam_role_policy.api_logs]
}

# `live` serves users; `candidate` is what CI evaluates before promoting (design.md M5).
resource "aws_lambda_alias" "api" {
  for_each         = toset(["live", "candidate"])
  name             = each.key
  function_name    = aws_lambda_function.api.function_name
  function_version = aws_lambda_function.api.version

  lifecycle {
    ignore_changes = [function_version] # moved by CI
  }
}

resource "aws_lambda_function_url" "api" {
  for_each           = aws_lambda_alias.api
  function_name      = aws_lambda_function.api.function_name
  qualifier          = each.value.name
  authorization_type = "NONE" # app-level Bearer token + daily quota instead (R7)
  invoke_mode        = "RESPONSE_STREAM"
}

# Public function URLs need both permissions (design.md R17, verified against provider 6.69).
# Created one after another: parallel AddPermission calls on a just-created function fail
# with ResourceConflictException (seen on the first `make up`).
resource "aws_lambda_permission" "url_live" {
  statement_id           = "FunctionUrlPublic-live"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  qualifier              = aws_lambda_alias.api["live"].name
  principal              = "*"
  function_url_auth_type = "NONE"
  depends_on             = [aws_lambda_function_url.api]
}

resource "aws_lambda_permission" "invoke_live" {
  statement_id             = "FunctionUrlInvoke-live"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  qualifier                = aws_lambda_alias.api["live"].name
  principal                = "*"
  invoked_via_function_url = true
  depends_on               = [aws_lambda_permission.url_live]
}

resource "aws_lambda_permission" "url_candidate" {
  statement_id           = "FunctionUrlPublic-candidate"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  qualifier              = aws_lambda_alias.api["candidate"].name
  principal              = "*"
  function_url_auth_type = "NONE"
  depends_on             = [aws_lambda_permission.invoke_live]
}

resource "aws_lambda_permission" "invoke_candidate" {
  statement_id             = "FunctionUrlInvoke-candidate"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  qualifier                = aws_lambda_alias.api["candidate"].name
  principal                = "*"
  invoked_via_function_url = true
  depends_on               = [aws_lambda_permission.url_candidate]
}

moved {
  from = aws_lambda_permission.url["live"]
  to   = aws_lambda_permission.url_live
}
moved {
  from = aws_lambda_permission.url_invoke["live"]
  to   = aws_lambda_permission.invoke_live
}
moved {
  from = aws_lambda_permission.url["candidate"]
  to   = aws_lambda_permission.url_candidate
}
moved {
  from = aws_lambda_permission.url_invoke["candidate"]
  to   = aws_lambda_permission.invoke_candidate
}
