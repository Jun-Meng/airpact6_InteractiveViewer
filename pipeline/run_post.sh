#!/bin/bash
# ============================================================
# run_post.sh — post-forecast hook for the Northwest Air Quality Forecast viewer
#
# Runs after the daily AIRPACT/CMAQ forecast finishes on Kamiak:
#   1. post-process the surface ACONC (O3) + AELMO (PM25) files
#   2. publish the web artifacts to the public host (not complete in this script,currently using a separate script: publish_cloudflare.sh)
#
# Wire it in as the last step of the forecast SLURM chain
# (sbatch --dependency=afterok:<forecast_jobid> run_post.sh)
# or from cron once the forecast files land.
#
# >>> Set every line marked  # EDIT  for your environment. <<<
# ============================================================

#SBATCH --job-name=ap6-postprocess
#SBATCH --partition=meng                 # EDIT partition
#SBATCH --time=00:20:00
#SBATCH --mem=8G
#SBATCH --cpus-per-task=2
#SBATCH --output=ap6-post-%j.log

set -euo pipefail

# ---- cycle date (YYYYMMDD); default today, or pass as arg ----
CYCLE="${1:-$(date +%Y%m%d)}"

# ---- inputs: real Kamiak forecast directories for this cycle.
#      Each species lives in its own subdir; files are per-day (3x each) and
#      the post-processor concatenates them in valid-time order. ----
FORECAST_DIR="/data/project/airpact/AP6_outputs/cycle_${CYCLE}_3day_forecast"
PM_FILES=( "$FORECAST_DIR"/PM25_only/PM25_AELMO_*.nc )
O3_FILES=( "$FORECAST_DIR"/O3_only/O3_ACONC_*.nc )

# ---- staging dir for generated artifacts ----
# Kamiak note: you cannot `mkdir /scratch/$USER` directly — scratch requires a
# "workspace" created with mkworkspace, and /home is only 10 GB. This resolves a
# writable location automatically. Override by exporting AQF_STAGE to any dir you
# own; for a daily operational job, a folder under your /data/project/airpact lab
# space is the tidiest (persistent, no workspace churn).
STAGE="/data/project/airpact/jmeng/Visualization/web_out/$CYCLE"

SCRIPT_DIR="/data/project/airpact/jmeng/Visualization/pipeline"  # EDIT (where postprocess_airquality.py lives)

# ---- publish target (one of the two below) ----
WEB_RSYNC="webuser@web.example.wsu.edu:/var/www/aqf/data"               # EDIT  (campus VM)
# S3_BUCKET="s3://my-aqf-bucket/data"                                   # EDIT  (object storage alt)

# ---- python environment: needs Python >=3.9 with numpy netCDF4 pyproj rasterio.
# A bare `module load python` or the system `python` may be Python 2 / <3.9 and
# will fail to even parse this pipeline. Use a conda-forge env. One-time setup:
#   module load anaconda3
#   conda create -y -n aqf -c conda-forge python=3.11 numpy netcdf4 pyproj rasterio
module load anaconda3                                                   # EDIT module name
source "$(conda info --base)/etc/profile.d/conda.sh"                    # makes `conda activate` work in scripts
conda activate aqf                                                      # EDIT env name
# fail loudly if the wrong interpreter is active, instead of running Python 2
python -c 'import sys; assert sys.version_info[:2] >= (3,9), sys.version' \
  || { echo "ERROR: active python is $(python --version 2>&1) at $(which python)"; \
       echo "       conda env 'aqf' is not active. Check: conda env list" >&2; exit 1; }

echo "[$(date)] post-processing cycle $CYCLE"
echo "  PM25: ${#PM_FILES[@]} file(s) in $FORECAST_DIR/PM25_only"
echo "  O3  : ${#O3_FILES[@]} file(s) in $FORECAST_DIR/O3_only"
test -e "${PM_FILES[0]}" || { echo "ERROR: no PM25 files matched under $FORECAST_DIR/PM25_only" >&2; exit 1; }
test -e "${O3_FILES[0]}" || { echo "ERROR: no O3 files matched under $FORECAST_DIR/O3_only" >&2; exit 1; }

#create files for web viewer
mkdir -p "$STAGE"
python "$SCRIPT_DIR/postprocess_airquality.py" \
    --cycle "$CYCLE" \
    --pm25  "${PM_FILES[@]}" \
    --o3    "${O3_FILES[@]}" \
    --outdir "$STAGE"

#create a shareable HTML that embeds the current forecasting cycle
python "$SCRIPT_DIR/build_embed.py" --data "$STAGE" --html "$SCRIPT_DIR/pnw-air-forecast.html" --out "$STAGE/forecast_${CYCLE}.html"


# ---- publish: not completed yet ----
# Option A: rsync to a campus web server. Push the cycle folder, then flip
# a stable "latest" symlink so the site URL never has to change.
#rsync -av --partial "$STAGE/" "$WEB_RSYNC/$CYCLE/"
#ssh "${WEB_RSYNC%%:*}" "ln -sfn '$CYCLE' '${WEB_RSYNC#*:}/latest'"       # latest -> CYCLE

# Option B: object storage (uncomment, set content-encoding for the .bin gzip)
# gzip -k -9 "$STAGE"/*.bin
# aws s3 cp "$STAGE/" "$S3_BUCKET/$CYCLE/" --recursive --exclude "*.bin"
# aws s3 cp "$STAGE/" "$S3_BUCKET/$CYCLE/" --recursive --exclude "*" --include "*.bin.gz" \
#     --content-encoding gzip --metadata-directive REPLACE

echo "[$(date)] published. Site DATA_URL -> https://<host>/aqf/data/latest/manifest.json"
