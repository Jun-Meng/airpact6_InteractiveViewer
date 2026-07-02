#!/bin/bash
#SBATCH --job-name=ap6-publish2web
#SBATCH --partition=meng
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --output=/data/project/airpact/jmeng/Visualization/logs/ap6_postpub_%j.log
# ============================================================
# postprocess_and_publish.sh — final step of the AIRPACT-6 daily pipeline.
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

# ---- 2) publish web_out/<cycle> to Cloudflare Pages (meng has internet) ----
bash "$PIPE/publish_cloudflare.sh" "$CYCLE"

echo "[$(date)] done -> https://nw-air-forecast.pages.dev/"
