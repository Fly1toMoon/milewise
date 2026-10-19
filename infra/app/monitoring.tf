# Alarms → SNS email. Custom metrics come from the app's EMF log lines (milewise.obs).

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email # AWS sends a confirmation email once
}

resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name          = "${local.name}-api-errors"
  alarm_description   = "Unhandled errors in /ask"
  namespace           = "Milewise"
  metric_name         = "error"
  dimensions          = { service = "api" }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# OpenAI spend is invisible to AWS Budgets (R20): alarm on the app's own cost metric.
resource "aws_cloudwatch_metric_alarm" "daily_llm_cost" {
  alarm_name          = "${local.name}-daily-llm-cost"
  alarm_description   = "LLM spend recorded by the app exceeded the daily limit"
  namespace           = "Milewise"
  metric_name         = "cost_usd"
  dimensions          = { service = "api" }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.daily_cost_limit_usd + var.public_daily_cost_limit_usd
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
