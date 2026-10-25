#!/usr/bin/env bash
# Deploy the hosted demo (sample mode) to Cloud Run: build both images with Cloud Build, push
# them to Artifact Registry, replace the service from service.yaml, print the URL.
# Idempotent. Needs gcloud logged in with rights on the project.
#   deploy/gcp/deploy.sh [PROJECT_ID] [REGION]
# The project comes from the argument, else GCP_PROJECT in the environment, else GCP_PROJECT in
# the repo's .env (gitignored); it is never committed. Region likewise (default
# asia-southeast1, a region where Cloud Run maps custom domains).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# DEMO_DOMAIN (argument-free, from the environment or .env) maps a custom domain if set.
# Read one KEY=value from .env without sourcing it (other lines need not be shell-safe).
env_get() { [ -f "$ROOT/.env" ] && sed -n "s/^$1=//p" "$ROOT/.env" | tail -n 1 | tr -d "\"'" || true; }
PROJECT="${1:-${GCP_PROJECT:-$(env_get GCP_PROJECT)}}"
REGION="${2:-${GCP_REGION:-$(env_get GCP_REGION)}}"
REGION="${REGION:-asia-southeast1}"
DOMAIN="${DEMO_DOMAIN:-$(env_get DEMO_DOMAIN)}"
if [ -z "$PROJECT" ]; then
  echo "No GCP project: set GCP_PROJECT in .env (see .env.example) or pass it as the first argument." >&2
  exit 2
fi
REPO_NAME=tiresias
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

# Least privilege, and it works where an organisation policy disables Cloud Build's default
# service account: builds run as tiresias-build (push images, write logs, read the uploaded
# source) and the service runs as tiresias-run, which has no role (the demo calls no Google API).
BUILD_SA="tiresias-build@${PROJECT}.iam.gserviceaccount.com"
RUN_SA="tiresias-run@${PROJECT}.iam.gserviceaccount.com"
step "Service accounts"
gcloud iam service-accounts describe "$BUILD_SA" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud iam service-accounts create tiresias-build --project "$PROJECT" \
       --display-name "Tiresias demo: Cloud Build (push images, logs)"
gcloud iam service-accounts describe "$RUN_SA" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud iam service-accounts create tiresias-run --project "$PROJECT" \
       --display-name "Tiresias demo: Cloud Run runtime (no roles)"
for role in roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:${BUILD_SA}" \
    --role "$role" --condition None --format none
done

step "Build agent and cognition images (tag ${TAG})"
gcloud builds submit "$ROOT" --project "$PROJECT" --region "$REGION" \
  --service-account "projects/${PROJECT}/serviceAccounts/${BUILD_SA}" \
  --config "$ROOT/deploy/gcp/cloudbuild.yaml" --substitutions "_REPO=${REPO},_TAG=${TAG}"

step "Deploy the Cloud Run service"
spec="$(mktemp)"
sed -e "s#AGENT_IMAGE#${REPO}/agent:${TAG}#" -e "s#COGNITION_IMAGE#${REPO}/cognition:${TAG}#" \
  -e "s#RUN_SERVICE_ACCOUNT#${RUN_SA}#" \
  "$ROOT/deploy/gcp/service.yaml" > "$spec"
gcloud run services replace "$spec" --project "$PROJECT" --region "$REGION"

url="$(gcloud run services describe tiresias --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
step "Check"
curl -sf "$url/health" && echo

if [ -n "$DOMAIN" ]; then
  step "Domain mapping ${DOMAIN}"
  gcloud beta run domain-mappings describe --domain "$DOMAIN" --project "$PROJECT" --region "$REGION" \
      >/dev/null 2>&1 \
    || gcloud beta run domain-mappings create --service tiresias --domain "$DOMAIN" \
         --project "$PROJECT" --region "$REGION"
  gcloud beta run domain-mappings describe --domain "$DOMAIN" --project "$PROJECT" --region "$REGION" \
    --format 'table(status.resourceRecords[].name, status.resourceRecords[].type, status.resourceRecords[].rrdata)'
  url="https://${DOMAIN}"
fi
printf '\nDemo: %s\n' "$url"
