#!/usr/bin/env bash
# Deploy HIT to a GCP Compute Engine VM.
# Edit the variables below, then run: bash scripts/deploy.sh
set -euo pipefail

# ── Configure these ───────────────────────────────────────────────────────────
PROJECT="your-gcp-project-id"       # gcloud projects list
ZONE="europe-west2-b"               # pick zone closest to your users
BUCKET="hit-data-prod"              # GCS bucket name (must be globally unique)
VM_NAME="hit-app"
MACHINE="n2-standard-4"            # 4 vCPU, 16 GB — scale up for larger cities
DISK_GB=100
IMAGE_REPO="gcr.io/$PROJECT/hit"   # container image path
IMAGE_TAG="$IMAGE_REPO:latest"
# ─────────────────────────────────────────────────────────────────────────────

log() { echo "▶ $*"; }

log "Project: $PROJECT | Zone: $ZONE | Bucket: $BUCKET"
gcloud config set project "$PROJECT" --quiet

# ── Enable required APIs ──────────────────────────────────────────────────────
log "Enabling APIs…"
gcloud services enable \
    compute.googleapis.com \
    secretmanager.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com \
    --quiet

# ── Build and push Docker image ───────────────────────────────────────────────
log "Building Docker image…"
docker build -t "$IMAGE_TAG" .

log "Pushing to $IMAGE_TAG…"
gcloud auth configure-docker --quiet
docker push "$IMAGE_TAG"

# ── Create GCS bucket (skip if exists) ───────────────────────────────────────
if ! gsutil ls -b "gs://$BUCKET" &>/dev/null; then
    log "Creating bucket gs://$BUCKET…"
    gsutil mb -p "$PROJECT" -l "${ZONE%-*}" "gs://$BUCKET"
else
    log "Bucket gs://$BUCKET already exists"
fi

# ── Upload local data to GCS (skip files already there) ───────────────────────
if [[ -d data ]]; then
    log "Syncing local data/ → gs://$BUCKET/data/"
    gsutil -m rsync -r -x '\.DS_Store$' data/ "gs://$BUCKET/data/"
fi

# ── Create CDS_API_KEY secret (prompts if not set) ───────────────────────────
if ! gcloud secrets describe CDS_API_KEY --project="$PROJECT" &>/dev/null; then
    log "Creating Secret Manager secret CDS_API_KEY…"
    read -rsp "Paste your CDS API key: " CDS_KEY; echo
    echo -n "$CDS_KEY" | gcloud secrets create CDS_API_KEY \
        --data-file=- --project="$PROJECT"
else
    log "Secret CDS_API_KEY already exists"
fi

# ── Firewall rule for port 8501 (skip if exists) ─────────────────────────────
if ! gcloud compute firewall-rules describe allow-hit-8501 --project="$PROJECT" &>/dev/null; then
    log "Creating firewall rule for port 8501…"
    gcloud compute firewall-rules create allow-hit-8501 \
        --project="$PROJECT" \
        --allow tcp:8501 \
        --target-tags=hit-app \
        --description="Allow inbound traffic to HIT Streamlit app"
fi

# ── Grant VM service account access to secrets and bucket ─────────────────────
SA="$(gcloud compute project-info describe --format='value(defaultServiceAccount)')"
log "Granting roles to default SA: $SA"

gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$SA" \
    --role="roles/secretmanager.secretAccessor" --quiet

gsutil iam ch "serviceAccount:$SA:roles/storage.objectAdmin" "gs://$BUCKET"

# ── Create VM ─────────────────────────────────────────────────────────────────
if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" &>/dev/null; then
    log "VM $VM_NAME already exists — to redeploy, delete it first:"
    log "  gcloud compute instances delete $VM_NAME --zone=$ZONE --project=$PROJECT"
else
    log "Creating VM $VM_NAME…"
    gcloud compute instances create "$VM_NAME" \
        --project="$PROJECT" \
        --zone="$ZONE" \
        --machine-type="$MACHINE" \
        --boot-disk-size="${DISK_GB}GB" \
        --boot-disk-type=pd-ssd \
        --image-family=debian-12 \
        --image-project=debian-cloud \
        --scopes=cloud-platform \
        --tags=hit-app \
        --metadata="BUCKET=$BUCKET,IMAGE=$IMAGE_TAG" \
        --metadata-from-file=startup-script=scripts/startup.sh
fi

# ── Print access URL ──────────────────────────────────────────────────────────
EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
    --zone="$ZONE" --project="$PROJECT" \
    --format='value(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null || echo "pending")

log ""
log "Done. App will be available at:"
log "  http://$EXTERNAL_IP:8501"
log ""
log "Startup takes ~3 min on first boot (Docker install + image pull)."
log "To watch progress:  gcloud compute ssh $VM_NAME --zone=$ZONE -- tail -f /var/log/hit-startup.log"
