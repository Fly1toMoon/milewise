-- Per-day request and cost counters for the public endpoint (design.md R7, R20).
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE ops.daily_usage (
  day       date PRIMARY KEY,
  requests  int NOT NULL DEFAULT 0,
  cost_usd  numeric(10, 6) NOT NULL DEFAULT 0
);
