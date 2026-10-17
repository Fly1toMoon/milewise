-- Award charts as structured data (design.md §2.1). Loaded from data/charts/** by
-- `milewise load-charts`; every row can be traced back to its source table cell.
CREATE SCHEMA IF NOT EXISTS fare;

CREATE TABLE fare.award_charts (
  chart_id        text PRIMARY KEY,                 -- 'NH-INTL-OWN-RT@2025-06-24'
  chart_family    text NOT NULL,                    -- 'NH-INTL-OWN' (zones/seasons are per family)
  airline         char(2) NOT NULL CHECK (airline IN ('NH', 'JL')),
  award_type      text NOT NULL,                    -- 'own_intl' | 'partner_intl' | 'domestic'
  trip_type       text NOT NULL CHECK (trip_type IN ('RT', 'OW')),
  pricing_basis   text NOT NULL CHECK (pricing_basis IN ('zone_pair', 'route', 'distance')),
  price_kind      text NOT NULL CHECK (price_kind IN ('fixed', 'floor')),
  booking_from    date NOT NULL,
  booking_to      date,                             -- NULL = current
  source_url      text NOT NULL,
  fetched_at      timestamptz NOT NULL,
  source_sha256   char(64) NOT NULL,
  parser_version  text NOT NULL
);

CREATE TABLE fare.zones (
  chart_family  text NOT NULL,
  zone_code     text NOT NULL,
  name_ja       text NOT NULL,
  season_group  text,                               -- NULL for Japan (Zone 1)
  PRIMARY KEY (chart_family, zone_code)
);

CREATE TABLE fare.zone_members (
  chart_family  text NOT NULL,
  zone_code     text NOT NULL,
  match_kind    text NOT NULL CHECK (match_kind IN ('country', 'city', 'airport')),
  match_value   text NOT NULL,
  PRIMARY KEY (chart_family, match_kind, match_value),
  FOREIGN KEY (chart_family, zone_code) REFERENCES fare.zones
);

CREATE TABLE fare.award_rates (
  rate_id        bigserial PRIMARY KEY,
  chart_id       text NOT NULL REFERENCES fare.award_charts ON DELETE CASCADE,
  origin_zone    text,
  dest_zone      text,
  origin_match   text[],
  dest_match     text[],
  flight_numbers text[],
  distance_min   int,
  distance_max   int,
  cabin          text NOT NULL CHECK (cabin IN ('Y', 'PY', 'C', 'F')),
  season         text NOT NULL CHECK (season IN ('L', 'R', 'H', 'ANY')),
  miles          int  NOT NULL CHECK (miles > 0),
  source_ref     text NOT NULL,
  CHECK (
    (origin_zone IS NOT NULL AND dest_zone IS NOT NULL AND origin_match IS NULL AND distance_min IS NULL) OR
    (origin_match IS NOT NULL AND dest_match IS NOT NULL AND origin_zone IS NULL AND distance_min IS NULL) OR
    (distance_min IS NOT NULL AND distance_max IS NOT NULL AND origin_zone IS NULL AND origin_match IS NULL)
  )
);
CREATE INDEX award_rates_zone_idx ON fare.award_rates (chart_id, origin_zone, dest_zone, cabin);

CREATE TABLE fare.season_calendar (
  chart_family  text NOT NULL,
  season_group  text NOT NULL,
  season        text NOT NULL CHECK (season IN ('L', 'R', 'H')),
  date_from     date NOT NULL,
  date_to       date NOT NULL CHECK (date_to >= date_from),
  EXCLUDE USING gist (chart_family WITH =, season_group WITH =,
                      daterange(date_from, date_to, '[]') WITH &&)
);
