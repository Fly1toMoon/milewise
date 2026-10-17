-- Extensions required by every environment (local docker and Aurora).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- fare.season_calendar exclusion constraint
