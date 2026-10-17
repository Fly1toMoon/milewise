#!/usr/bin/env bash
# Eval-gated release of the api function (design.md M5). Used by `make` and by CI.
#
#   scripts/release.sh up?                 exit 0 if the app stack exists (CI skips otherwise)
#   scripts/release.sh candidate <tag>     push image, update both functions, publish a version,
#                                          point the `candidate` alias at it
#   scripts/release.sh candidate-url       URL of the candidate alias
#   scripts/release.sh eval-env            print DB_* / OPENAI_API_KEY exports for a remote eval
#   scripts/release.sh promote             point `live` at the version `candidate` serves
#
# Everything is resolved through AWS APIs (no Terraform state), so the CI deploy role
# needs no access to the state bucket.
set -euo pipefail

API=milewise-api
INGEST=milewise-ingest
REGION=${AWS_REGION:-ap-northeast-1}

account() { aws sts get-caller-identity --query Account --output text; }
repo() { echo "$(account).dkr.ecr.${REGION}.amazonaws.com/milewise"; }

cmd=${1:?usage: release.sh up?|candidate <tag>|candidate-url|eval-env|promote}
case "$cmd" in
  up\?)
    aws lambda get-function --function-name "$API" --query 'Configuration.FunctionName' --output text >/dev/null 2>&1
    ;;
  candidate)
    tag=${2:?image tag}
    r=$(repo)
    aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${r%%/*}" >/dev/null
    # Lambda rejects OCI indexes with attestation manifests, so attestations are disabled.
    docker build --platform linux/arm64 --provenance=false --sbom=false -t "$r:$tag" .
    docker push "$r:$tag" >/dev/null
    for fn in "$API" "$INGEST"; do
      aws lambda update-function-code --function-name "$fn" --image-uri "$r:$tag" --query LastUpdateStatus --output text >/dev/null
      aws lambda wait function-updated-v2 --function-name "$fn"
    done
    # The ingest function has no alias: it runs $LATEST (ingestion is idempotent and re-runnable).
    v=$(aws lambda publish-version --function-name "$API" --description "$tag" --query Version --output text)
    aws lambda update-alias --function-name "$API" --name candidate --function-version "$v" >/dev/null
    echo "candidate -> $API version $v ($tag)"
    ;;
  candidate-url)
    aws lambda get-function-url-config --function-name "$API" --qualifier candidate --query FunctionUrl --output text
    ;;
  eval-env)
    cluster=$(aws rds describe-db-clusters --db-cluster-identifier milewise --query 'DBClusters[0].[DBClusterArn,MasterUserSecret.SecretArn]' --output text)
    echo "export DB_CLUSTER_ARN=$(cut -f1 <<<"$cluster")"
    echo "export DB_SECRET_ARN=$(cut -f2 <<<"$cluster")"
    # The judge calls OpenAI; reuse the key stored for Lambda instead of a second copy in CI.
    echo "export OPENAI_API_KEY=$(aws secretsmanager get-secret-value --secret-id milewise/openai-api-key --query SecretString --output text)"
    ;;
  promote)
    v=$(aws lambda get-alias --function-name "$API" --name candidate --query FunctionVersion --output text)
    aws lambda update-alias --function-name "$API" --name live --function-version "$v" >/dev/null
    echo "live -> $API version $v"
    ;;
  *)
    echo "unknown command: $cmd" >&2; exit 2 ;;
esac
