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

variable "chat_model" {
  description = "Bedrock model or inference profile for the agent and the reranker (design.md §4.7)."
  type        = string
  default     = "jp.amazon.nova-2-lite-v1:0"
}

variable "judge_model" {
  description = "Bedrock inference profile for the eval judge (CI deploy role only)."
  type        = string
  default     = "apac.amazon.nova-pro-v1:0"
}

variable "embedding_model" {
  description = "Bedrock embedding model the rules index is built with (must match the app's EMBEDDING_MODEL)."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
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
