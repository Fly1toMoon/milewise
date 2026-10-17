# S3 (raw pages, bootstrap bucket) → SQS → ingest Lambda (same image, POST /events)
# with a dead-letter queue. Ingestion is idempotent, so redelivery is harmless (§2.2).

locals {
  raw_bucket     = local.bootstrap.raw_docs_bucket
  raw_bucket_arn = "arn:aws:s3:::${local.bootstrap.raw_docs_bucket}"
  ingest_timeout = 300
}

resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${local.name}-ingest-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${local.name}-ingest"
  visibility_timeout_seconds = 6 * local.ingest_timeout
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 3
  })
}

data "aws_iam_policy_document" "ingest_queue" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest.arn]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [local.raw_bucket_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id
  policy    = data.aws_iam_policy_document.ingest_queue.json
}

# The only notification configuration on the raw bucket; removed again by `make down`.
resource "aws_s3_bucket_notification" "raw" {
  bucket = local.raw_bucket

  dynamic "queue" {
    for_each = ["nh/", "jl/"]
    content {
      queue_arn     = aws_sqs_queue.ingest.arn
      events        = ["s3:ObjectCreated:*"]
      filter_prefix = queue.value
      filter_suffix = ".html"
    }
  }

  depends_on = [aws_sqs_queue_policy.ingest]
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.name}-ingest"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "ingest" {
  name               = "${local.name}-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "ingest" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${local.raw_bucket_arn}/nh/*", "${local.raw_bucket_arn}/jl/*"]
  }
  statement {
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
    resources = [aws_sqs_queue.ingest.arn]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

resource "aws_iam_role_policy" "ingest_app" {
  name   = "app"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.app_common.json
}

resource "aws_lambda_function" "ingest" {
  function_name = "${local.name}-ingest"
  role          = aws_iam_role.ingest.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["arm64"]
  memory_size   = 1024
  timeout       = local.ingest_timeout

  environment {
    variables = merge(local.common_env, {
      AWS_LWA_INVOKE_MODE = "buffered" # SQS events need a buffered JSON response
    })
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ingest.name
  }

  lifecycle {
    ignore_changes = [image_uri]
  }

  depends_on = [aws_iam_role_policy.ingest]
}

resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn        = aws_sqs_queue.ingest.arn
  function_name           = aws_lambda_function.ingest.arn
  batch_size              = 5
  function_response_types = ["ReportBatchItemFailures"]
}
