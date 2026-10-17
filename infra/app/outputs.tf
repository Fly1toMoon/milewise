output "live_url" {
  value = aws_lambda_function_url.api["live"].function_url
}

output "candidate_url" {
  value = aws_lambda_function_url.api["candidate"].function_url
}

output "cluster_arn" {
  value = aws_rds_cluster.this.arn
}

output "db_secret_arn" {
  value = aws_rds_cluster.this.master_user_secret[0].secret_arn
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

output "ingest_queue_url" {
  value = aws_sqs_queue.ingest.id
}
