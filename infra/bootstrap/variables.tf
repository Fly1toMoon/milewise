variable "region" {
  type    = string
  default = "ap-northeast-1"
}

variable "budget_email" {
  description = "Where AWS Budgets alerts are sent."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget. Alerts at 50% / 100% actual and 100% forecasted."
  type        = number
  default     = 30
}

variable "github_oidc_subject_prefix" {
  description = <<-EOT
    Prefix of the GitHub OIDC `sub` claim for the repository. New repositories use the
    immutable form "repo:<owner>@<owner_id>/<repo>@<repo_id>", which keeps working after a
    rename and cannot be matched by another repository later created under the same name.
    Read it with: gh api repos/<owner>/<repo>/actions/oidc/customization/sub
  EOT
  type        = string
  validation {
    condition     = can(regex("^repo:[^@/]+@[0-9]+/[^@/]+@[0-9]+$", var.github_oidc_subject_prefix))
    error_message = "Expected the immutable form repo:<owner>@<owner_id>/<repo>@<repo_id>."
  }
}

variable "create_github_oidc_provider" {
  description = "Set false if this account already has the token.actions.githubusercontent.com OIDC provider."
  type        = bool
  default     = true
}
