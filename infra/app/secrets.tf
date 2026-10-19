# The OpenAI key is a container only: its value is written with the CLI
# (`make put-openai-key`) so it never appears in Terraform state (design.md R20).
resource "aws_secretsmanager_secret" "openai" {
  name                    = "${local.name}/openai-api-key"
  description             = "Project-scoped OpenAI API key used by the Lambda functions"
  recovery_window_in_days = 0 # recreate with the same name after destroy (R15)
}

# Bearer token for POST /ask. Generated here so `make up` works unattended; it is in the
# (encrypted, private) state, which is acceptable for a demo access token, unlike the API key.
resource "random_password" "app_token" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "app_token" {
  name                    = "${local.name}/app-token"
  description             = "Bearer token required by POST /ask"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app_token" {
  secret_id     = aws_secretsmanager_secret.app_token.id
  secret_string = random_password.app_token.result
}

# Cloudflare Turnstile secret key (bot check, design.md §4.8). Container only: the value is
# written with `make put-turnstile-secret`, like the OpenAI key. Unused while
# turnstile_site_key is empty.
resource "aws_secretsmanager_secret" "turnstile" {
  name                    = "${local.name}/turnstile-secret"
  description             = "Cloudflare Turnstile secret key for the public page"
  recovery_window_in_days = 0
}
