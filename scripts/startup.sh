#!/usr/bin/env bash
# Runs on every VM boot. Installs deps, mounts GCS bucket, pulls image, starts app.
set -euo pipefail

log() { echo "[startup] $*" | tee -a /var/log/hit-startup.log; }

# ── Read instance metadata ────────────────────────────────────────────────────
META=http://metadata.google.internal/computeMetadata/v1
HEADERS=(-H "Metadata-Flavor: Google")

PROJECT=$(curl -sf "$META/project/project-id" "${HEADERS[@]}")
BUCKET=$(curl -sf  "$META/instance/attributes/BUCKET"    "${HEADERS[@]}")
IMAGE=$(curl -sf   "$META/instance/attributes/IMAGE"     "${HEADERS[@]}")

log "project=$PROJECT  bucket=$BUCKET  image=$IMAGE"

# ── Install Docker (idempotent) ───────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    log "Installing Docker…"
    curl -fsSL https://get.docker.com | sh
fi

# ── Install gcsfuse (idempotent) ──────────────────────────────────────────────
if ! command -v gcsfuse &>/dev/null; then
    log "Installing gcsfuse…"
    apt-get update -qq
    apt-get install -y -qq fuse
    CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
    echo "deb https://packages.cloud.google.com/apt gcsfuse-${CODENAME} main" \
        > /etc/apt/sources.list.d/gcsfuse.list
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
    apt-get update -qq
    apt-get install -y -qq gcsfuse
fi

# ── Mount GCS bucket ──────────────────────────────────────────────────────────
mkdir -p /mnt/hit-data
if ! mountpoint -q /mnt/hit-data; then
    log "Mounting gs://$BUCKET → /mnt/hit-data"
    gcsfuse --implicit-dirs "$BUCKET" /mnt/hit-data
fi

# ── Fetch CDS API key from Secret Manager ────────────────────────────────────
log "Fetching CDS_API_KEY from Secret Manager…"
CDS_API_KEY=$(gcloud secrets versions access latest \
    --secret=CDS_API_KEY --project="$PROJECT" 2>/dev/null || true)
if [[ -z "$CDS_API_KEY" ]]; then
    log "WARNING: CDS_API_KEY secret not found — ERA5 fetches will fail"
fi

# ── Configure Docker auth for Artifact Registry / GCR ───────────────────────
gcloud auth configure-docker --quiet

# ── Pull latest image ─────────────────────────────────────────────────────────
log "Pulling $IMAGE…"
docker pull "$IMAGE"

# ── Stop old container if running ─────────────────────────────────────────────
docker rm -f hit-app 2>/dev/null || true

# ── Start app ─────────────────────────────────────────────────────────────────
log "Starting hit-app…"
docker run -d \
    --name hit-app \
    --restart always \
    -p 8501:8501 \
    -e CDS_API_KEY="$CDS_API_KEY" \
    -v /mnt/hit-data:/app/data \
    "$IMAGE"

log "Done — app running on :8501"
