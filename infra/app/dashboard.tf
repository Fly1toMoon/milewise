# One CloudWatch dashboard for the questions an operator asks first (design.md §4.5):
# is it up, is it fast, what does it cost, is the post-check firing. Custom metrics come
# from the app's EMF lines (namespace Milewise, dimension service=api). The first three
# dashboards in an account are free.

locals {
  emf = { namespace = "Milewise", dim = ["service", "api"] }
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "Questions and errors (per hour)"
          region = var.region
          stat   = "Sum"
          period = 3600
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.api.function_name, { label = "Lambda invocations" }],
            [local.emf.namespace, "latency_ms", local.emf.dim[0], local.emf.dim[1], { stat = "SampleCount", label = "answered questions" }],
            [local.emf.namespace, "error", local.emf.dim[0], local.emf.dim[1], { label = "app errors", color = "#d62728" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.api.function_name, { label = "Lambda errors" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "Answer latency (ms)"
          region = var.region
          period = 3600
          metrics = [
            [local.emf.namespace, "latency_ms", local.emf.dim[0], local.emf.dim[1], { stat = "p50", label = "p50" }],
            [local.emf.namespace, "latency_ms", local.emf.dim[0], local.emf.dim[1], { stat = "p95", label = "p95" }],
            ["AWS/Lambda", "InitDuration", "FunctionName", aws_lambda_function.api.function_name, { stat = "Maximum", label = "cold start (init) max" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "Model spend recorded by the app (USD per day)"
          region = var.region
          stat   = "Sum"
          period = 86400
          metrics = [
            [local.emf.namespace, "cost_usd", local.emf.dim[0], local.emf.dim[1], { label = "cost_usd" }],
          ]
          annotations = {
            horizontal = [{ label = "daily cost limit (429 above)", value = var.daily_cost_limit_usd }]
          }
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Tokens and post-check failures (per hour)"
          region = var.region
          stat   = "Sum"
          period = 3600
          metrics = [
            [local.emf.namespace, "input_tokens", local.emf.dim[0], local.emf.dim[1], { label = "input tokens" }],
            [local.emf.namespace, "output_tokens", local.emf.dim[0], local.emf.dim[1], { label = "output tokens" }],
            [local.emf.namespace, "postcheck_failed", local.emf.dim[0], local.emf.dim[1], { label = "post-check failed", yAxis = "right", color = "#d62728" }],
          ]
        }
      },
      {
        type = "log", x = 0, y = 12, width = 24, height = 6
        properties = {
          title  = "Latest questions"
          region = var.region
          query  = "SOURCE '${aws_cloudwatch_log_group.api.name}' | filter event = 'ask' | fields @timestamp, request_id, latency_ms, cost_usd, postcheck_failed, error, error_type | sort @timestamp desc | limit 50"
          view   = "table"
        }
      },
    ]
  })
}

output "dashboard_url" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.main.dashboard_name}"
}
