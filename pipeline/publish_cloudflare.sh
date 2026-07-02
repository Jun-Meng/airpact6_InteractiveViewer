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
INCLUDE_COGS=0                                                              # 1 to also publish .tif downloads

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

# ---- assemble the site directory ----
SITE="$(mktemp -d)"
trap 'rm -rf "$SITE"' EXIT
mkdir -p "$SITE/data/latest"

# viewer -> index.html, pointed at the relative data path (same-origin, no CORS)
sed 's#const DATA_URL = "";#const DATA_URL = "data/latest/manifest.json";#' \
    "$VIEWER" > "$SITE/index.html"

# data: manifest + packed binaries (the viewer needs these)
cp "$SRC/manifest.json" "$SRC"/*.bin "$SITE/data/latest/"
[ "$INCLUDE_COGS" = "1" ] && cp "$SRC"/*.tif "$SITE/data/latest/" 2>/dev/null || true

# cache headers (Pages honors _headers). The page and manifest keep the same
# filename every cycle, so they must revalidate; the binaries are cycle-dated
# (unique names), so they can cache hard.
cat > "$SITE/_headers" <<HDR
/index.html
  Cache-Control: no-cache
/data/latest/manifest.json
  Cache-Control: no-cache
/data/latest/*.bin
  Cache-Control: public, max-age=86400
HDR

echo "  site assembled: $(du -sh "$SITE" | cut -f1)"

# ---- deploy (first run auto-creates the project) ----
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
# ============================================================
