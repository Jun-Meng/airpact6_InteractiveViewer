#!/bin/bash
#SBATCH --job-name=ap6-publish2web
#SBATCH --partition=meng
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --output=/data/project/airpact/jmeng/Visualization/logs/ap6_postpub_%j.log
#SBATCH --mail-type=FAIL,TIMEOUT
#SBATCH --mail-user=jun.meng@dal.ca
# ============================================================
# postprocess_and_publish.sh — final step of the AIRPACT-6 daily air quality forecast pipeline.
# 
# This pipeline is currently submitted by jun.meng - July 2 2026
#
# Orchestrates the two working scripts, in order:
#   1) run_post.sh          -> web_out/<cycle>/ (manifest, bins, COGs, daily)
#                              and the embedded forecast_<cycle>.html
#   2) publish_cloudflare.sh -> deploys web_out/<cycle> to Cloudflare Pages
#
# The meng partition has outbound internet (verified), so both steps run here
# in one job — no login node, no cron. Chain it to the forecast:
#   sbatch --dependency=afterok:<final_cmaq_jobid> postprocess_and_publish.sh <YYYYMMDD>
#
# One-time: create the log dir ->  mkdir -p /data/project/airpact/jmeng/Visualization/logs
# ============================================================
set -euo pipefail

CYCLE="${1:-$(date +%Y%m%d)}"
PIPE=/data/project/airpact/jmeng/Visualization/pipeline

echo "[$(date)] post-process + publish for cycle $CYCLE on $(hostname)"

# ---- 1) post-process: build web_out/<cycle> + embedded HTML (no internet) ----
bash "$PIPE/run_post.sh" "$CYCLE"

# ---- 1b) verification: pair yesterday's AirNow obs with archived forecasts ----
# Non-fatal: a hiccup here must never block the daily forecast publish.
# Needs ~/.airnow_env (AIRNOW_API_KEY) and the aqf env; meng partition has internet.
(
  set +e
  source /etc/profile.d/modules.sh 2>/dev/null
  module load anaconda3 2>/dev/null
  source "$(conda info --base)/etc/profile.d/conda.sh" && conda activate aqf
  YDAY=$(date -d "yesterday" +%Y%m%d)
  python "$PIPE/verify_airnow.py" --date "$YDAY" \
    || echo "[$(date)] WARNING: verification failed for $YDAY (continuing)"
)

# ---- 2) publish web_out/<cycle> to Cloudflare Pages (meng has internet) ----
bash "$PIPE/publish_cloudflare.sh" "$CYCLE"

echo "[$(date)] done -> https://nw-air-forecast.pages.dev/"
