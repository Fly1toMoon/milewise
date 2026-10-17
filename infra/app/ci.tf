# Extra permissions for the GitHub deploy role (created in bootstrap) that need ARNs
# from this stack: write eval results through the Data API and read the app token to
# call the candidate URL. Removed together with the stack.
data "aws_iam_policy_document" "deploy_eval" {
  statement {
    actions   = ["rds-data:ExecuteStatement", "rds-data:BatchExecuteStatement", "rds-data:BeginTransaction", "rds-data:CommitTransaction", "rds-data:RollbackTransaction"]
    resources = [aws_rds_cluster.this.arn]
  }
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_rds_cluster.this.master_user_secret[0].secret_arn,
      aws_secretsmanager_secret.app_token.arn,
    ]
  }
}

resource "aws_iam_role_policy" "deploy_eval" {
  name   = "app-stack-eval"
  role   = "milewise-github-deploy"
  policy = data.aws_iam_policy_document.deploy_eval.json
}
