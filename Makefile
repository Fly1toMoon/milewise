SHELL := /bin/bash
export AWS_PROFILE ?= milewise
export AWS_REGION  ?= ap-northeast-1

TF            := terraform
BOOTSTRAP_DIR := infra/bootstrap
APP_DIR       := infra/app
SPIKE_DIR     := infra/spikes/aurora-data-api
BACKEND_HCL   := infra/backend.hcl

.PHONY: help
help:  ## Show targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

# ---------- Python ----------
.PHONY: setup test lint fmt
setup:  ## Install Python 3.12 deps with uv
	uv sync

test:  ## Run unit tests
	uv run pytest -q

lint:  ## Ruff + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

fmt:  ## Format code (Python + Terraform)
	uv run ruff format .
	uv run ruff check --fix .
	$(TF) fmt -recursive infra

# ---------- Local DB ----------
.PHONY: db-up db-down
db-up:  ## Start local Postgres + pgvector
	docker compose up -d --wait db

db-down:  ## Stop local Postgres (keeps volume)
	docker compose down

# ---------- App (local) ----------
.PHONY: migrate load-charts ingest-local import-raw ask eval parse-ana
migrate:  ## Apply db/migrations to $$DATABASE_URL
	uv run $(ENV_FILE) milewise migrate

load-charts:  ## Load data/charts/** into the database (idempotent)
	uv run $(ENV_FILE) milewise load-charts

import-raw:  ## Normalize browser-saved bundles (data/raw/*/manifest.json) into raw/<airline>/
	uv run python scripts/import_raw_bundle.py

ingest-local:  ## Chunk + embed the newest raw/ copy of every rules page into the local DB
	uv run $(ENV_FILE) milewise ingest-local

ask:  ## Ask a question end to end: make ask Q="東京→ロンドンのビジネスは何マイル？"
	uv run $(ENV_FILE) milewise ask "$(Q)"

eval:  ## Run the eval set (writes eval/results/latest.md; exit 1 below thresholds)
	uv run $(ENV_FILE) milewise eval

parse-ana:  ## Re-parse the newest raw ANA page into data/charts/NH-INTL-OWN
	uv run python scripts/charts/parse_ana.py $$(ls -t raw/nh/ana-intl-award-chart_*.html | head -1)

# ---------- Preflight ----------
ENV_FILE := $(if $(wildcard .env),--env-file .env,)

.PHONY: whoami check-openai
whoami:  ## Show which AWS account/role you are using
	aws sts get-caller-identity

check-openai:  ## Verify the OpenAI key: model access, tool calling, structured outputs, embeddings (< $$0.001)
	uv run $(ENV_FILE) python scripts/check_openai.py

# ---------- Bootstrap stack (persistent, never destroyed) ----------
.PHONY: bootstrap-init bootstrap-plan bootstrap-apply bootstrap-backend
bootstrap-init:  ## First-time init (local state)
	$(TF) -chdir=$(BOOTSTRAP_DIR) init

bootstrap-plan:
	$(TF) -chdir=$(BOOTSTRAP_DIR) plan

bootstrap-apply:  ## Create state bucket, budgets, ECR, buckets, GitHub OIDC
	$(TF) -chdir=$(BOOTSTRAP_DIR) apply

bootstrap-backend:  ## After first apply: move bootstrap state into the S3 bucket it created
	@bucket=$$($(TF) -chdir=$(BOOTSTRAP_DIR) output -raw tfstate_bucket) && \
	printf 'bucket       = "%s"\nregion       = "%s"\nuse_lockfile = true\nencrypt      = true\n' "$$bucket" "$(AWS_REGION)" > $(BACKEND_HCL) && \
	printf 'terraform {\n  backend "s3" {}\n}\n' > $(BOOTSTRAP_DIR)/backend.tf && \
	$(TF) -chdir=$(BOOTSTRAP_DIR) init -migrate-state -force-copy \
	  -backend-config=../backend.hcl -backend-config="key=bootstrap/terraform.tfstate" && \
	rm -f $(BOOTSTRAP_DIR)/terraform.tfstate $(BOOTSTRAP_DIR)/terraform.tfstate.backup && \
	echo "Bootstrap state now in s3://$$bucket/bootstrap/terraform.tfstate"

# ---------- Spike: Aurora Serverless v2 (min 0 ACU) + Data API + pgvector ----------
.PHONY: spike-up spike-run spike-resume spike-down
spike-up:  ## Create throwaway Aurora cluster (~10-15 min)
	$(TF) -chdir=$(SPIKE_DIR) init -backend-config=../../backend.hcl -backend-config="key=spikes/aurora-data-api.tfstate"
	$(TF) -chdir=$(SPIKE_DIR) apply

spike-run:  ## Run Data API + pgvector checks against the spike cluster
	uv run python scripts/spike_data_api.py \
	  --cluster-arn $$($(TF) -chdir=$(SPIKE_DIR) output -raw cluster_arn) \
	  --secret-arn  $$($(TF) -chdir=$(SPIKE_DIR) output -raw master_secret_arn) \
	  --database    $$($(TF) -chdir=$(SPIKE_DIR) output -raw database_name)

spike-resume:  ## Wait until the cluster auto-pauses (0 ACU), then time the first query
	uv run python scripts/spike_data_api.py --measure-resume \
	  --cluster-arn $$($(TF) -chdir=$(SPIKE_DIR) output -raw cluster_arn) \
	  --secret-arn  $$($(TF) -chdir=$(SPIKE_DIR) output -raw master_secret_arn) \
	  --database    $$($(TF) -chdir=$(SPIKE_DIR) output -raw database_name)

spike-down:  ## Destroy the spike cluster
	$(TF) -chdir=$(SPIKE_DIR) destroy

# ---------- App stack (destroyable): make up / make down ----------
IMAGE_TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || date +dev-%Y%m%d%H%M%S)
TFOUT      = $(TF) -chdir=$(APP_DIR) output -raw
CLOUD_DB   = DB_CLUSTER_ARN=$$($(TFOUT) cluster_arn) DB_SECRET_ARN=$$($(TFOUT) db_secret_arn)

.PHONY: image deploy app-init app-plan app-apply put-openai-key upload-raw restore smoke up down
# Lambda rejects OCI indexes with attestation manifests, so attestations are disabled.
image:  ## Build the arm64 image and push it to ECR as $(IMAGE_TAG)
	@repo=$$($(TF) -chdir=$(BOOTSTRAP_DIR) output -raw ecr_repository_url) && \
	aws ecr get-login-password | docker login --username AWS --password-stdin $${repo%%/*} && \
	docker build --platform linux/arm64 --provenance=false --sbom=false -t $$repo:$(IMAGE_TAG) . && \
	docker push $$repo:$(IMAGE_TAG)

deploy: image  ## Code-only release: new image -> both functions -> new version -> live + candidate
	@repo=$$($(TF) -chdir=$(BOOTSTRAP_DIR) output -raw ecr_repository_url) && \
	for fn in milewise-api milewise-ingest; do \
	  aws lambda update-function-code --function-name $$fn --image-uri $$repo:$(IMAGE_TAG) \
	    --query 'LastUpdateStatus' --output text && \
	  aws lambda wait function-updated-v2 --function-name $$fn || exit 1; \
	done && \
	v=$$(aws lambda publish-version --function-name milewise-api --description "$(IMAGE_TAG)" \
	  --query Version --output text) && \
	for alias in candidate live; do \
	  aws lambda update-alias --function-name milewise-api --name $$alias --function-version $$v \
	    --query 'AliasArn' --output text || exit 1; \
	done && echo "milewise-api version $$v ($(IMAGE_TAG)) is live"

app-init:
	$(TF) -chdir=$(APP_DIR) init -backend-config=../backend.hcl -backend-config="key=app/terraform.tfstate"

app-plan: app-init  ## Plan the app stack for $(IMAGE_TAG)
	$(TF) -chdir=$(APP_DIR) plan -var image_tag=$(IMAGE_TAG)

app-apply: app-init  ## Create/update the app stack (Aurora, Lambda, SQS, alarms)
	$(TF) -chdir=$(APP_DIR) apply -var image_tag=$(IMAGE_TAG)

put-openai-key:  ## Copy the OpenAI key from .env into Secrets Manager (not via Terraform)
	uv run $(ENV_FILE) python scripts/put_openai_secret.py $$($(TFOUT) openai_secret_id)

upload-raw:  ## Upload raw rules pages; S3 events trigger the ingest Lambda
	aws s3 sync raw/ s3://$$($(TF) -chdir=$(BOOTSTRAP_DIR) output -raw raw_docs_bucket)/ \
	  --exclude "*" --include "nh/*.html" --include "jl/*.html"

restore:  ## Rebuild cloud data: migrations, charts, then re-ingest raw pages
	$(CLOUD_DB) uv run milewise migrate
	$(CLOUD_DB) uv run milewise load-charts
	$(MAKE) upload-raw

smoke:  ## Health, auth and one real question against the live URL
	uv run python scripts/smoke.py $$($(TFOUT) live_url) $$($(TFOUT) app_token_secret_arn)

up: image app-apply put-openai-key restore smoke  ## Everything, from an empty app stack

down: app-init  ## Destroy the app stack (bootstrap stays: state, buckets, ECR, budgets)
	$(TF) -chdir=$(APP_DIR) destroy -var image_tag=$(IMAGE_TAG)
