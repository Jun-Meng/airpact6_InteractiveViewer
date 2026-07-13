#!/bin/bash
# ============================================================
# publish_cloudflare.sh — push the latest forecast cycle to Cloudflare Pages.
#
# Run on a Kamiak LOGIN node (needs outbound internet; compute nodes are walled
# off, so this is separate from the sbatch post-processing job). Assembles a
# small site dir (viewer as index.html + the cycle's data under data/latest/)
# and deploys it with Wrangler.
#
# One-time setup is documented at the bottom of this file.
# Set every line marked  # EDIT.
# ============================================================
set -euo pipefail

# ---- config ----
PROJECT="nw-air-forecast"                                                   # EDIT Cloudflare Pages project
PROD_BRANCH="main"                                                          # EDIT production branch
STAGE_ROOT="/data/project/airpact/jmeng/Visualization/web_out"             # EDIT parent of web_out/<cycle>
VIEWER="/data/project/airpact/jmeng/Visualization/pipeline/pnw-air-forecast.html"  # EDIT viewer template
FUNCTIONS="/data/project/airpact/jmeng/Visualization/pipeline/functions"    # EDIT Pages Functions (AirNow proxy)
VERIFY_HTML="/data/project/airpact/jmeng/Visualization/pipeline/verify.html" # EDIT verification page
INCLUDE_COGS=1                                                              # 1 to publish latest-cycle .tif downloads (data.html)

# ---- credentials (cron has a bare environment, so source them from a file) ----
# Create ~/.cloudflare_env  (chmod 600) containing:
#   export CLOUDFLARE_API_TOKEN=********
#   export CLOUDFLARE_ACCOUNT_ID=********
source "$HOME/.cloudflare_env"

# ---- make module + conda usable under cron's minimal shell ----
source /etc/profile.d/modules.sh 2>/dev/null || true
module load anaconda3 2>/dev/null || true                                   # EDIT module name
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate aqf                                                          # EDIT env (must have nodejs + wrangler)

# ---- choose the cycle: arg, or newest web_out/<cycle> ----
CYCLE="${1:-$(ls -1 "$STAGE_ROOT" 2>/dev/null | grep -E '^[0-9]{8}$' | sort | tail -1)}"
[ -n "${CYCLE:-}" ] || { echo "no cycle found under $STAGE_ROOT" >&2; exit 1; }
SRC="$STAGE_ROOT/$CYCLE"
test -f "$SRC/manifest.json" || { echo "no manifest.json in $SRC" >&2; exit 1; }
echo "[$(date)] publishing cycle $CYCLE from $SRC"

# ---- assemble the project directory ----
# Layout required by wrangler for Functions:  <root>/functions/  next to the
# static dir (NOT inside it); we deploy <root>/public as the assets.
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
SITE="$ROOT/public"
mkdir -p "$SITE/data/latest"

# viewer -> index.html, pointed at the relative data path (same-origin, no CORS)
sed 's#const DATA_URL = "";#const DATA_URL = "data/latest/manifest.json";#' \
    "$VIEWER" > "$SITE/index.html"

# stage one cycle: manifest + gzipped bins. The .bin.gz is cached next to the
# source bin in web_out, so each cycle is compressed only once (first publish);
# the viewer inflates client-side with pako (~25 MB -> ~8 MB first load).
stage_cycle(){ # $1 = source cycle dir, $2 = dest dir
  mkdir -p "$2"
  cp "$1/manifest.json" "$2/"
  local b
  for b in "$1"/*.bin; do
    [ -e "$b" ] || continue
    [ -f "$b.gz" ] || gzip -9 -c "$b" > "$b.gz"
    cp "$b.gz" "$2/"
  done
}

# data: manifest + packed binaries (the viewer needs these)
stage_cycle "$SRC" "$SITE/data/latest"
# COGs (GeoTIFF downloads for data.html): LATEST cycle only, and skip any file
# over Cloudflare Pages' 25 MiB per-file limit (deploy would fail otherwise).
if [ "$INCLUDE_COGS" = "1" ]; then
  for T in "$SRC"/*.tif; do
    [ -e "$T" ] || continue
    SZ=$(stat -c%s "$T" 2>/dev/null || stat -f%z "$T")
    if [ "$SZ" -lt 26214400 ]; then
      cp "$T" "$SITE/data/latest/"
    else
      echo "  WARNING: $(basename "$T") is $((SZ/1048576)) MiB (> Pages 25 MiB cap) — skipped"
    fi
  done
fi

# archive: every completed cycle under data/<cycle>/, plus an index the viewer
# uses for its "Forecast cycle" selector. Wrangler uploads are content-hashed,
# so re-deploying old cycles costs nothing after their first upload.
ARCHIVED=""
for C in $(ls -1 "$STAGE_ROOT" | grep -E '^[0-9]{8}$' | sort); do
  [ -f "$STAGE_ROOT/$C/manifest.json" ] || continue
  stage_cycle "$STAGE_ROOT/$C" "$SITE/data/$C"
  ARCHIVED="$ARCHIVED $C"
done
printf '[%s]\n' "$(printf '"%s",' $ARCHIVED | sed 's/,$//')" > "$SITE/data/cycles.json"
echo "  archived cycles:$ARCHIVED"

# verification page + nightly obs-vs-forecast stats (written by verify_airnow.py)
[ -f "$VERIFY_HTML" ] && cp "$VERIFY_HTML" "$SITE/verify.html"
[ -f "$(dirname "$VIEWER")/about.html" ] && cp "$(dirname "$VIEWER")/about.html" "$SITE/about.html"
[ -f "$(dirname "$VIEWER")/data.html" ] && cp "$(dirname "$VIEWER")/data.html" "$SITE/data.html"

# social-preview image for link cards (LinkedIn/Twitter og:image; 1200x627 png)
[ -f "$(dirname "$VIEWER")/og-preview.png" ] && cp "$(dirname "$VIEWER")/og-preview.png" "$SITE/og-preview.png"

# robots.txt: without it, Pages' index-fallback serves HTML at /robots.txt,
# which some crawlers (incl. LinkedIn's) treat as "do not crawl".
cat > "$SITE/robots.txt" <<'ROB'
User-agent: *
Allow: /
ROB

# real 404 page: its presence also disables the serve-index-for-every-path
# fallback, so missing assets return proper 404s to crawlers.
cat > "$SITE/404.html" <<'NF'
<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Not found</title></head>
<body style="font-family:sans-serif;background:#0b0f12;color:#e8eef4;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<p>Page not found &mdash; <a href="/" style="color:#5b9bec">Northwest Air Quality Forecast</a></p></body></html>
NF
if [ -f "$STAGE_ROOT/verification/summary.json" ]; then
  mkdir -p "$SITE/data/verification"
  cp "$STAGE_ROOT/verification/summary.json" "$SITE/data/verification/"
  [ -f "$STAGE_ROOT/verification/sites.json" ] && cp "$STAGE_ROOT/verification/sites.json" "$SITE/data/verification/"
fi

# Pages Functions (AirNow obs proxy -> /api/obs). Needs the AIRNOW_API_KEY
# project secret; without it the endpoint returns 503 and the viewer just
# greys out the monitor layer.
[ -d "$FUNCTIONS" ] && cp -r "$FUNCTIONS" "$ROOT/functions"

# cache headers (Pages honors _headers). The page and manifest keep the same
# filename every cycle, so they must revalidate; the binaries are cycle-dated
# (unique names), so they can cache hard.
cat > "$SITE/_headers" <<HDR
/index.html
  Cache-Control: no-cache
/data/latest/manifest.json
  Cache-Control: no-cache
/data/:cycle/*.bin.gz
  Cache-Control: public, max-age=31536000, immutable
/data/latest/*.bin.gz
  Cache-Control: public, max-age=86400
/data/cycles.json
  Cache-Control: no-cache
/verify.html
  Cache-Control: no-cache
/data.html
  Cache-Control: no-cache
/data/latest/*.tif
  Cache-Control: public, max-age=86400
/data/verification/summary.json
  Cache-Control: no-cache
/data/verification/sites.json
  Cache-Control: no-cache
HDR

echo "  site assembled: $(du -sh "$SITE" | cut -f1)"

# ---- deploy (first run auto-creates the project) ----
# run from $ROOT so wrangler picks up $ROOT/functions
cd "$ROOT"
wrangler pages deploy "$SITE" \
    --project-name "$PROJECT" \
    --branch "$PROD_BRANCH" \
    --commit-dirty=true

echo "[$(date)] published cycle $CYCLE -> https://$PROJECT.pages.dev/"

# ============================================================
# ONE-TIME SETUP
# ------------------------------------------------------------
# 1) Cloudflare account (free) -> Workers & Pages -> create a Pages project
#    named to match $PROJECT, type "Direct Upload", production branch = main.
#
# 2) API token: My Profile -> API Tokens -> Create Token -> template
#    "Cloudflare Pages: Edit". Copy the token and your Account ID, then on a
#    Kamiak login node:
#       printf 'export CLOUDFLARE_API_TOKEN=%s\nexport CLOUDFLARE_ACCOUNT_ID=%s\n' \
#         'TOKEN' 'ACCOUNT_ID' > ~/.cloudflare_env
#       chmod 600 ~/.cloudflare_env
#
# 3) Node + Wrangler in the conda env:
#       conda install -n aqf -c conda-forge nodejs
#       conda activate aqf && npm install -g wrangler
#       wrangler --version          # confirm
#
# 4) First publish (also creates the project if it doesn't exist):
#       ./publish_cloudflare.sh 20260628
#
# 5) AirNow observations (monitor-site overlay). Get a free API key at
#    https://docs.airnowapi.org (Sign up), then store it as a Pages secret
#    (one time, after the project exists). Keep AIRNOW_API_KEY literally —
#    it is the secret's NAME; wrangler prompts "Enter a secret value:" and
#    that prompt is where you paste the actual key:
#       wrangler pages secret put AIRNOW_API_KEY --project-name nw-air-forecast
#    Until the secret is set, /api/obs returns 503 and the viewer shows the
#    monitor toggle greyed out ("obs unavailable").
# ============================================================
