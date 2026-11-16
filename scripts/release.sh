#!/usr/bin/env bash
# Eval-gated release of the api function (design.md M5). Used by `make` and by CI.
#
#   scripts/release.sh up?                 exit 0 if the app stack exists (CI skips otherwise)
#   scripts/release.sh eval-env            print the EVAL_UPLOAD export (where eval runs are kept)
#   scripts/release.sh index               sync raw pages from S3, build build/index reusing the
#                                          embeddings of the last released index
#   scripts/release.sh candidate <tag>     build + push the image (code + build/index), publish a
#                                          version, point the `candidate` alias at it
#   scripts/release.sh candidate-url       URL of the candidate alias
#   scripts/release.sh promote             point `live` at the version `candidate` serves, and keep
#                                          its index as the embedding cache for the next build
#   scripts/release.sh needs-eval          after `index`: print "true" unless the change since the
#                                          live commit cannot affect answers (see below)
#
# Everything is resolved through AWS APIs (no Terraform state), so the CI deploy role
# needs no access to the state bucket.
set -euo pipefail

API=milewise-api
REGION=${AWS_REGION:-ap-northeast-1}

account() { aws sts get-caller-identity --query Account --output text; }
repo() { echo "$(account).dkr.ecr.${REGION}.amazonaws.com/milewise"; }
raw_bucket() { echo "milewise-raw-docs-$(account)"; }
artifacts() { echo "s3://milewise-artifacts-$(account)"; }

cmd=${1:?usage: release.sh up?|eval-env|index|candidate <tag>|candidate-url|promote}
case "$cmd" in
  up\?)
    aws lambda get-function --function-name "$API" --query 'Configuration.FunctionName' --output text >/dev/null 2>&1
    ;;
  index)
    # One sync per prefix: the deploy role may list the raw bucket only under nh/ and jl/.
    for p in nh jl; do
      aws s3 sync "s3://$(raw_bucket)/$p/" "raw/$p/" --only-show-errors --exclude "*" --include "*.html"
    done
    rm -rf build/index-cache
    aws s3 sync "$(artifacts)/index/live/" build/index-cache/ --only-show-errors
    uv run milewise build-index --raw raw --out build/index --cache build/index-cache
    ;;
  candidate)
    tag=${2:?image tag}
    test -f build/index/manifest.json || { echo "build/index missing: run 'release.sh index' or 'make index'" >&2; exit 1; }
    r=$(repo)
    # Tags are immutable, so a re-run of the same commit reuses the image it already pushed.
    if aws ecr describe-images --repository-name "${r#*/}" --image-ids imageTag="$tag" >/dev/null 2>&1; then
      echo "image $tag already in ECR; reusing it"
    else
      aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${r%%/*}" >/dev/null
      # Lambda rejects OCI indexes with attestation manifests, so attestations are disabled.
      docker build --platform linux/arm64 --provenance=false --sbom=false -t "$r:$tag" .
      docker push "$r:$tag" >/dev/null
    fi
    aws lambda update-function-code --function-name "$API" --image-uri "$r:$tag" --query LastUpdateStatus --output text >/dev/null
    aws lambda wait function-updated-v2 --function-name "$API"
    index=$(python3 -c 'import json; print(json.load(open("build/index/manifest.json"))["content_sha256"][:12])')
    v=$(aws lambda publish-version --function-name "$API" --description "$tag index:$index" --query Version --output text)
    aws lambda update-alias --function-name "$API" --name candidate --function-version "$v" >/dev/null
    echo "candidate -> $API version $v ($tag)"
    ;;
  candidate-url)
    aws lambda get-function-url-config --function-name "$API" --qualifier candidate --query FunctionUrl --output text
    ;;
  eval-env)
    # Embeddings and the judge run on Bedrock with the caller's IAM role: no API key in CI.
    echo "export EVAL_UPLOAD=$(artifacts)/eval/"
    ;;
  promote)
    v=$(aws lambda get-alias --function-name "$API" --name candidate --query FunctionVersion --output text)
    aws lambda update-alias --function-name "$API" --name live --function-version "$v" >/dev/null
    echo "live -> $API version $v"
    # The index now serving users is the embedding cache for the next build.
    if [ -f build/index/manifest.json ]; then
      aws s3 sync build/index/ "$(artifacts)/index/live/" --only-show-errors --delete
    fi
    ;;
  needs-eval)
    # The eval may be skipped only when every file changed since the commit `live` serves is
    # on the list below (page, bot check, logging, infra, tests, docs) AND the rules index
    # just built is the one live serves. Anything else, or anything we cannot tell, evals:
    # a path missing from the list costs one extra eval, never an untested release.
    # The base is the live commit, not the previous push, so a release whose eval failed
    # cannot ride along with a later docs-only push.
    say() { echo "needs-eval: $*" >&2; }
    v=$(aws lambda get-alias --function-name "$API" --name live --query FunctionVersion --output text 2>/dev/null) || { say "no live alias"; echo true; exit 0; }
    desc=$(aws lambda get-function-configuration --function-name "$API" --qualifier "$v" --query Description --output text)
    base=${desc%% *}
    git cat-file -e "$base^{commit}" 2>/dev/null || { say "live commit '$base' not in history"; echo true; exit 0; }
    built=$(python3 -c 'import json; print(json.load(open("build/index/manifest.json"))["content_sha256"])')
    live=$(python3 -c 'import json; print(json.load(open("build/index-cache/manifest.json"))["content_sha256"])' 2>/dev/null || true)
    [ "$built" = "$live" ] || { say "rules index differs from live"; echo true; exit 0; }
    safe='^([^/]*\.md|docs/.*|src/milewise/static/.*|src/milewise/(guard|obs)\.py|infra/.*|\.github/.*|tests/.*|eval/results/.*|scripts/.*|edge/.*|Makefile|\.gitignore|\.env\.example|\.claude/.*)$'
    changed=$(git diff --name-only "$base" HEAD)
    other=$(printf '%s\n' "$changed" | grep -Ev "$safe" | grep -v '^$' || true)
    if [ -n "$other" ]; then
      say "changed since live ($base):"; printf '  %s\n' $other >&2; echo true
    else
      say "only page/infra/docs/test files changed since live ($base); skipping eval"; echo false
    fi
    ;;
  *)
    echo "unknown command: $cmd" >&2; exit 2 ;;
esac
