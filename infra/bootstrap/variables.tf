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

variable "github_repository" {
  description = "GitHub repository allowed to assume the CI roles, as \"owner/name\"."
  type        = string
}

variable "create_github_oidc_provider" {
  description = "Set false if this account already has the token.actions.githubusercontent.com OIDC provider."
  type        = bool
  default     = true
}
