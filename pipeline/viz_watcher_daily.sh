#!/bin/bash
#SBATCH --job-name=viz_watcher
#SBATCH --partition=meng
#SBATCH --time=06:15:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --output=/data/project/airpact/jmeng/Visualization/logs/viz_watcher_%j.log
#SBATCH --mail-type=FAIL,TIMEOUT
#SBATCH --mail-user=jun.meng@dal.ca
# ============================================================
# viz_watcher_daily.sh — self-rearming watcher for the air quality viewer.
#
# Runs by Jun Meng. Polls Priom's AP6_outputs for the newest forecast cycle whose
# PM25_only/O3_only files are complete but not yet published, then submits the
# post-process + publish job. Re-arms itself for the same time tomorrow.
#
# Priom's operational pipeline is NOT modified. This only READS his output.
#
# First launch (once):   sbatch viz_watcher_daily.sh
# ============================================================
set -uo pipefail   # NOT -e: a transient error must not skip the re-arm at the end

PIPE=/data/project/airpact/jmeng/Visualization/pipeline
WEBOUT=/data/project/airpact/jmeng/Visualization/web_out
AP6=/data/project/airpact/AP6_outputs
SELF=$PIPE/viz_watcher_daily.sh
ARM_TIME=07:00            # fixed daily start time (re-arm target); tune to your schedule

MAX_POLLS=12              # 12 * 30 min = 6 h polling window
INTERVAL=1800            # 30 min

POLL=0
OUTCOME=none   # none = polling window exhausted -> job exits 1 -> SLURM FAIL email

echo "[$(date)] viz watcher start on $(hostname)"

while [ $POLL -lt $MAX_POLLS ]; do
    POLL=$((POLL+1))

    # newest forecast cycle directory Priom has produced
    NEWEST=$(ls -1d "$AP6"/cycle_*_3day_forecast 2>/dev/null | sort -r | head -1)
    if [ -z "$NEWEST" ]; then
        echo "[$(date)] poll $POLL/$MAX_POLLS: no cycle dirs yet; sleeping"
        sleep $INTERVAL; continue
    fi
    CYCLE=$(basename "$NEWEST" | grep -oE '[0-9]{8}')

    if [ -f "$WEBOUT/$CYCLE/manifest.json" ]; then
        echo "[$(date)] newest cycle $CYCLE already published; done for today"
        OUTCOME=ok
        break
    fi

    PMN=$(ls "$NEWEST"/PM25_only/PM25_AELMO_*.nc 2>/dev/null | wc -l)
    O3N=$(ls "$NEWEST"/O3_only/O3_ACONC_*.nc  2>/dev/null | wc -l)
    if [ "$PMN" -ge 3 ] && [ "$O3N" -ge 3 ]; then
        echo "[$(date)] cycle $CYCLE ready (PM=$PMN O3=$O3N) -> submitting post-process + publish"
        if sbatch "$PIPE/postprocess_and_publish.sh" "$CYCLE"; then
            OUTCOME=ok
        else
            echo "[$(date)] ERROR: sbatch submit of postprocess_and_publish failed"
        fi
        break
    fi

    echo "[$(date)] poll $POLL/$MAX_POLLS: $CYCLE not ready yet (PM=$PMN O3=$O3N); sleeping"
    sleep $INTERVAL
done

# re-arm for tomorrow at the fixed time (self-perpetuating; no cron needed)
# ALWAYS re-arm before signalling failure — a missed day must not stop the chain.
echo "[$(date)] re-arming for tomorrow $ARM_TIME"
sbatch --begin="tomorrow $ARM_TIME" "$SELF"

if [ "$OUTCOME" != "ok" ]; then
    echo "[$(date)] no cycle published today (window exhausted or submit failed) -> exiting 1 for SLURM FAIL email"
    exit 1
fi
echo "[$(date)] watcher done"
