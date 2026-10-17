output "account_id" {
  value = local.account_id
}

output "tfstate_bucket" {
  value = aws_s3_bucket.this["tfstate"].bucket
}

output "raw_docs_bucket" {
  value = aws_s3_bucket.this["raw_docs"].bucket
}

output "artifacts_bucket" {
  value = aws_s3_bucket.this["artifacts"].bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "github_plan_role_arn" {
  value = aws_iam_role.github_plan.arn
}

output "github_deploy_role_arn" {
  value = aws_iam_role.github_deploy.arn
}
