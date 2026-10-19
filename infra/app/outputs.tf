output "live_url" {
  value = aws_lambda_function_url.api["live"].function_url
}

output "candidate_url" {
  value = aws_lambda_function_url.api["candidate"].function_url
}

output "openai_secret_id" {
  value = aws_secretsmanager_secret.openai.id
}

output "app_token_secret_arn" {
  value = aws_secretsmanager_secret.app_token.arn
}

output "api_function_name" {
  value = aws_lambda_function.api.function_name
}

output "usage_table" {
  value = aws_dynamodb_table.usage.name
}

output "quota_table" {
  value = aws_dynamodb_table.quota.name
}
