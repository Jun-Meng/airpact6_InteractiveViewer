# Viz pipeline — operator instructions

Audience: anyone running under the `jun.meng` Kamiak account (e.g. Priom while
testing). The visualization pipeline is Jun's; **do not edit anything in this
repo** — these commands only *run* it. It READS `AP6_outputs` and never
touches the operational forecast.

## Where things live

| Thing | Path |
|---|---|
| Repo checkout | `/data/project/airpact/jmeng/Visualization` |
| Pipeline scripts | `.../Visualization/pipeline/` |
| Per-cycle output | `.../Visualization/web_out/<YYYYMMDD>/` |
| Logs | `.../Visualization/logs/` |
| Forecast input (read-only) | `/data/project/airpact/AP6_outputs/cycle_<YYYYMMDD>_3day_forecast/` |

All commands below assume:

```bash
cd /data/project/airpact/jmeng/Visualization
```

Note: `/data` is mounted noexec — always run scripts as `bash script.sh` or
`sbatch script.sh`, never `./script.sh`.

## Normal operation (nothing to do)

A self-rearming SLURM job, the **viz watcher**, starts every day at 07:00,
polls for the newest complete forecast cycle (up to 10 h, every 15 min),
submits the processing job, publishes the site, and re-arms itself for
tomorrow. When it is healthy you do nothing. A FAIL email to Jun means no
cycle was published that day.

Check its state any time:

```bash
squeue -u jun.meng -n viz_watcher
```

Correct state: **exactly one** job — either PD (pending, waiting for 07:00)
or R (running, polling).

## Manually process + publish one cycle

Use when a cycle finished after the watcher gave up, or the watcher was down:

```bash
sbatch pipeline/postprocess_and_publish.sh 20260821   # cycle date YYYYMMDD
```

That one job does everything: reads the cycle from `AP6_outputs`, builds the
web artifacts into `web_out/<cycle>/`, runs verification, and deploys to
Cloudflare. Prerequisite: the cycle directory must have ≥3 files in each of
`PM25_only/PM25_AELMO_*.nc` and `O3_only/O3_ACONC_*.nc`.

Verify it worked:

```bash
squeue -u jun.meng                          # wait until the job is gone
ls web_out/20260821/manifest.json           # exists = processed
tail -30 logs/viz_watcher_*.log             # or the postprocess job log
```

Then hard-refresh https://nw-air-forecast.pages.dev/ (Cmd+Shift+R — the
manifest is cached) and confirm the cycle badge shows the new date.

## Stop the watcher (e.g. known-broken cycle)

```bash
squeue -u jun.meng -n viz_watcher    # note the JOBID(s)
scancel <JOBID>
```

Cancelling breaks the chain permanently — the watcher only re-arms itself, so
after any `scancel` it must be restarted by hand (next section). Prefer
letting it run: a broken/missing cycle just exhausts the polling window and
emails Jun; the chain survives.

## Start / restart the watcher

```bash
# arm for tomorrow 07:00 (the usual restart after a scancel):
sbatch --begin="tomorrow 07:00" pipeline/viz_watcher_daily.sh

# or start one polling right now (it still re-arms for tomorrow when done):
sbatch pipeline/viz_watcher_daily.sh

# then ALWAYS confirm exactly one exists:
squeue -u jun.meng -n viz_watcher
```

**Never leave two watchers queued** — duplicates submit duplicate publish
jobs. If you see more than one, `scancel` the extras.

## Rules and gotchas

- Publishing needs internet: only the **login node** and the **`meng`
  partition** have it. `postprocess_and_publish.sh` already targets the right
  partition — don't move it elsewhere.
- Don't `scancel` a running `postprocess_and_publish` job mid-flight; let it
  finish or the cycle is left half-built (rerunning it is the fix).
- Don't edit, `git pull`, or commit in this repo — Jun deploys changes from
  his side. If something looks broken, message Jun instead of fixing.
- SLURM `--begin` is a floor, not a guarantee — a watcher starting 08:30
  instead of 07:00 is normal when the partition is busy.
- Interactive shells: `source "$(conda info --base)/etc/profile.d/conda.sh"`
  then `conda activate aqf` (never `conda init`). Batch jobs handle this
  themselves.

Questions → Jun Meng, jun.meng@dal.ca.
