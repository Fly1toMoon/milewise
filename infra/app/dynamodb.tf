# Daily request / cost counters for POST /ask (design.md R7, §4.6): one item per JST day,
# updated with atomic ADD by milewise.quota, expired by TTL. On-demand: pennies per month.
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
