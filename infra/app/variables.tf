variable "region" {
  type    = string
  default = "ap-northeast-1"
}

variable "image_tag" {
  description = "ECR tag (git SHA) of the image to deploy on first create. CI updates code afterwards."
  type        = string
}

variable "alert_email" {
  description = "Email for CloudWatch alarms (SNS asks you to confirm the subscription once)."
  type        = string
}

variable "openai_model" {
  type    = string
  default = "gpt-6-luna"
}

variable "daily_request_limit" {
  description = "Requests/day to POST /ask, CI evals included (25 cases + retries per release)."
  type        = number
  default     = 300
}

variable "daily_cost_limit_usd" {
  type    = number
  default = 1
}

variable "log_retention_days" {
  type    = number
  default = 14
}
