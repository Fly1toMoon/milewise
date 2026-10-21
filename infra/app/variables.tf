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
  description = "Bedrock model or inference profile for the agent (design.md §4.7, §4.11)."
  type        = string
  default     = "zai.glm-5"
}

variable "rerank_model" {
  description = "Bedrock model or inference profile for the search_rules relevance filter."
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
  default     = "cohere.embed-multilingual-v3"
}

variable "public_daily_request_limit" {
  description = "Public (token-free) questions per JST day for all users together."
  type        = number
  default     = 1000
}

variable "public_daily_cost_limit_usd" {
  description = "Public model spend per JST day; with the request limit, whichever is reached first closes the day."
  type        = number
  default     = 2
}

variable "ip_window_limit" {
  description = "Public questions per IP (IPv6 /64) per 10 minutes."
  type        = number
  default     = 5
}

variable "ip_daily_limit" {
  description = "Public questions per IP per JST day."
  type        = number
  default     = 20
}

variable "turnstile_site_key" {
  description = "Cloudflare Turnstile site key; empty disables the bot check."
  type        = string
  default     = ""
}

variable "daily_request_limit" {
  description = "Owner-token requests/day (you and CI evals: ~32-64 per release), separate from the public budget."
  type        = number
  default     = 300
}

variable "daily_cost_limit_usd" {
  type    = number
  default = 3
}

variable "log_retention_days" {
  type    = number
  default = 14
}
