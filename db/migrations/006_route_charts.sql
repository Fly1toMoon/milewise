-- Route-based charts (JAL): rows are labelled routes, not zone pairs, and JAL publishes
-- both the basic miles (a floor) and the maximum with 特典航空券PLUS (a ceiling).
ALTER TABLE fare.award_charts DROP CONSTRAINT award_charts_price_kind_check;
ALTER TABLE fare.award_charts ADD CONSTRAINT award_charts_price_kind_check
  CHECK (price_kind IN ('fixed', 'floor', 'ceiling'));
ALTER TABLE fare.award_rates ADD COLUMN route_label text;
CREATE INDEX award_rates_route_idx ON fare.award_rates USING gin (dest_match);
