#!/usr/bin/env bash
# Deploy the hosted demo (sample mode) to Cloud Run: build both images with Cloud Build, push
# them to Artifact Registry, replace the service from service.yaml, print the URL.
# Idempotent. Needs gcloud logged in with rights on the project.
#   deploy/gcp/deploy.sh [PROJECT_ID] [REGION]
set -euo pipefail

PROJECT="${1:-${GCP_PROJECT:-tiresias-512703}}"
REGION="${2:-${GCP_REGION:-australia-southeast1}}"
REPO_NAME=tiresias
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="$(git -C "$ROOT" rev-parse --short HEAD)"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/${REPO_NAME}"

step() { printf '\n== %s\n' "$*"; }

step "APIs (Cloud Run, Cloud Build, Artifact Registry)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  --project "$PROJECT"

step "Artifact Registry repository ${REPO}"
gcloud artifacts repositories describe "$REPO_NAME" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO_NAME" --repository-format docker \
       --location "$REGION" --project "$PROJECT" --description "Tiresias demo images"

step "Build agent and cognition images (tag ${TAG})"
gcloud builds submit "$ROOT" --project "$PROJECT" --region "$REGION" \
  --config "$ROOT/deploy/gcp/cloudbuild.yaml" --substitutions "_REPO=${REPO},_TAG=${TAG}"

step "Deploy the Cloud Run service"
spec="$(mktemp)"
sed -e "s#AGENT_IMAGE#${REPO}/agent:${TAG}#" -e "s#COGNITION_IMAGE#${REPO}/cognition:${TAG}#" \
  "$ROOT/deploy/gcp/service.yaml" > "$spec"
gcloud run services replace "$spec" --project "$PROJECT" --region "$REGION"

url="$(gcloud run services describe tiresias --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
step "Check"
curl -sf "$url/health" && echo
printf '\nDemo: %s\n' "$url"
