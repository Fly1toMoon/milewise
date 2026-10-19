# Superseded by milewise-quota below (design.md §4.8). Kept for one release so the version
# still serving `live` during the rollout keeps working; remove after the next deploy.
resource "aws_dynamodb_table" "usage" {
  name         = "${local.name}-usage"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "day"

  attribute {
    name = "day"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# Guards for the public endpoint (milewise.guard, design.md §4.8): per-IP windows, site and
# owner day budgets, the answer cache, the kill switch and the once-a-day alert marker —
# one item each under a string key, all expired by TTL. On-demand: pennies per month.
resource "aws_dynamodb_table" "quota" {
  name         = "${local.name}-quota"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
