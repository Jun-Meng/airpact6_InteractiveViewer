# Northwest Air Quality Forecast Viewer

An interactive web map of the AIRPACT-6 air quality forecast (PM2.5 and ozone)
for the Pacific Northwest, updated automatically every day from CMAQ output on
the WSU Kamiak HPC. 

**Live site:** https://nw-air-forecast.pages.dev/

This repository is the **visualization pipeline**: it turns the operational
CMAQ forecast into web graphics and publishes them. It does not run the
forecast itself — that is the separate operational pipeline (owned by Priom),
which this project only reads from. This visualization pipeline was launched on July 2 2026,
and it automated the first forecast cycle on July 3 2026. 

This is an ongoing effort. Improvements and updates to the Viewer are implemented on a regular basis.

Author: Jun Meng

July 3 2026

---

## What it does

Every day, after the AIRPACT-6 forecast finishes, this pipeline:

1. reads the surface PM2.5 (`AELMO`) and ozone (`ACONC`) NetCDF files,
2. reprojects the 4 km Lambert Conformal grid to Web Mercator,
3. computes daily AQI products (24-hr PM2.5, 8-hr-max ozone),
4. packs everything into a compact web format, and
5. publishes it to Cloudflare Pages, where the interactive viewer serves it.

The viewer offers a 72-hour hourly animation, a Daily AQI view, AQI-category or
raw-concentration coloring (PM2.5 uses the AIRPACT5 banded scale: https://airpact.wsu.edu/map.html?v=0.005), click-to-query
readouts, and state/county overlays.

---

## Architecture / daily flow

```
  Priom's operational pipeline (runs as priom)          This pipeline (runs as jun.meng)
  --------------------------------------------          -----------------------------
  watcher (22:30) -> CMAQ -> postproc_3day.sh                 viz_watcher_daily.sh (07:00)
        writes:                                                     polls for completed cycle
        /data/project/airpact/AP6_outputs/                          |
          cycle_<CYCLE>_3day_forecast/                              v
            PM25_only/PM25_AELMO_*.nc  ----------- reads ----> postprocess_and_publish.sh
            O3_only/O3_ACONC_*.nc                                    |  run_post.sh
                                                                     |    -> web_out/<CYCLE>/
                                                                     |  publish_cloudflare.sh
                                                                     v    -> Cloudflare Pages
                                                              https://nw-air-forecast.pages.dev/
```

**Operation Strategy (important):** the two halves run as two different users.
Priom's pipeline writes `AP6_outputs`; this pipeline only *reads* it. This
pipeline writes `web_out/` and owns the Cloudflare account. Neither user writes
into the other's space. That is why publishing is driven by this polling
watcher (viz_watcher_daily) rather than by a job submitted from Priom's pipeline — a job Priom
submits would run as Priom and could not reach our env, token, or `web_out`.

---

## Repository layout

```
Visualization/
├── README.md
├── .gitignore
├── pipeline/
│   ├── postprocess_airquality.py   # CMAQ NetCDF -> manifest + bins + COGs + daily
│   ├── build_embed.py              # bake one cycle into a standalone HTML
│   ├── pnw-air-forecast.html       # the viewer (source; DATA_URL="")
│   ├── functions/api/obs.js        # Cloudflare Pages Function: AirNow obs proxy (/api/obs)
│   ├── verify_airnow.py            # nightly obs-vs-forecast pairing -> verification stats
│   ├── verify.html                 # verification page (bias/RMSE/r, published as /verify.html)
│   ├── run_post.sh                 # post-process wrapper (postproc + embed)
│   ├── publish_cloudflare.sh       # deploy web_out/<cycle> to Cloudflare Pages
│   ├── postprocess_and_publish.sh  # run_post.sh + publish (job the watcher submits)
│   └── viz_watcher_daily.sh        # self-rearming daily watcher
├── web_out/                        # generated per-cycle artifacts (git-ignored)
└── logs/                           # SLURM logs (git-ignored)
```

Generated data (`web_out/`, `*.bin`, `*.nc`, `*.tif`, `forecast_*.html`), logs,
and secrets (`~/.cloudflare_env`) are **not** tracked — see `.gitignore`.

---

## Key facts

- **Grid:** AIRPACT 4 km Lambert Conformal (GDTYP=2), 285×258 cells, surface layer.
  Warped to EPSG:3857 (~310×270) for the web.
- **Domain (lon/lat):** −125.92 to −109.59, 39.79 to 49.84 (WA, OR, ID + parts of MT, WY, NV, CA, UT).
- **Cadence:** hourly, 72 forecast hours per cycle (three daily files per species, concatenated).
- **Ozone units:** CMAQ ACONC is ppmV; the pipeline converts to ppb.
- **Daily AQI:** PM2.5 = 24-hr mean; ozone = MDA8 (max 8-hr running mean, windows 07:00–23:00 local); overall = worse of the two.
- **Environment:** conda env `aqf` (module `anaconda3`), Python ≥3.9 with `numpy netCDF4 pyproj rasterio`, plus `nodejs` + `wrangler` for publishing.
- **Partition:** `meng` (has outbound internet — required for the Cloudflare publish).

---

## One-time setup

```bash
# 1. conda env (Python geo stack + node + wrangler)
module load anaconda3
conda create -y -n aqf -c conda-forge python=3.11 numpy netcdf4 pyproj rasterio nodejs
conda activate aqf
npm install -g wrangler
wrangler --version                       # confirm

# 2. Cloudflare credentials (token from: My Profile -> API Tokens -> "Cloudflare Pages: Edit")
cat > ~/.cloudflare_env <<'EOF'
export CLOUDFLARE_API_TOKEN=********     # contact Jun Meng for the API_TOKEN and ACCOUNT_ID
export CLOUDFLARE_ACCOUNT_ID=********
EOF
chmod 600 ~/.cloudflare_env              # keep the token private; never commit it

# 3. log directory
mkdir -p /data/project/airpact/jmeng/Visualization/logs

# 4. AirNow API key (monitor-site observations; free account at https://docs.airnowapi.org)
#    Run this once after the project exists (keep AIRNOW_API_KEY literally — it is
#    the secret's *name*); wrangler then prompts "Enter a secret value:" and THAT
#    is where you paste the key you got from AirNow:
wrangler pages secret put AIRNOW_API_KEY --project-name nw-air-forecast

# 5. same AirNow key on Kamiak for the nightly verification job. Replace
#    YOUR_KEY below with the actual key from airnowapi.org (the same key you
#    stored as the Pages secret); AIRNOW_API_KEY stays literal:
echo 'export AIRNOW_API_KEY=YOUR_KEY' > ~/.airnow_env && chmod 600 ~/.airnow_env
# backfill verification from the oldest archived cycle (one time):
python pipeline/verify_airnow.py --backfill 20260627
```

The Cloudflare Pages project (`nw-air-forecast`, production branch `main`) is
created automatically on the first `wrangler pages deploy`.

---

## Running it

**Automatic (production):** launch the watcher once; it re-arms itself daily.

```bash
cd /data/project/airpact/jmeng/Visualization/pipeline
sbatch viz_watcher_daily.sh
```

Each day it wakes at `ARM_TIME`, polls `AP6_outputs` for the newest completed
cycle, and submits `postprocess_and_publish.sh`, which post-processes and
publishes it. Priom's pipeline is not modified.

**Manual (one cycle), for testing:**

```bash
# post-process + publish a specific cycle as one job
sbatch postprocess_and_publish.sh 20260628

# or the steps separately:
bash run_post.sh 20260628              # -> web_out/20260628/
bash publish_cloudflare.sh 20260628    # -> deploy to Cloudflare (run where there is internet)
```

**Local preview** (no server): copy a `web_out/<cycle>` folder to your laptop,
set `const DATA_URL = "manifest.json";` in a copy of the viewer, place it in the
folder, and `python3 -m http.server`.

> On Kamiak, run scripts with `bash script.sh` (not `./script.sh`) — `/data`
> may be mounted `noexec`. Git/Cloudflare operations must run on a **login node**
> (compute nodes reach the internet only on the `meng` partition).

---

## Tuning the watcher

Edit the top of `viz_watcher_daily.sh`. Priom's output normally lands by ~07:00,
so the defaults arm at 07:00 and poll a short window:

| variable      | meaning                          | current |
|---------------|----------------------------------|---------|
| `ARM_TIME`    | daily start time                 | 07:00   |
| `MAX_POLLS`   | number of polls                  | 8       |
| `INTERVAL`    | seconds between polls            | 900 (15 min) |
| `#SBATCH --time` | wall limit (must be ≥ MAX_POLLS×INTERVAL, or the re-arm won't fire) | 02:15:00 |

Rule of thumb: keep `--time` slightly above `MAX_POLLS × INTERVAL` so the job
always reaches the self-re-arm line at the end. Widen the window if a run is
ever missed because the output arrived late.

**Stop the watcher:** `scancel` both the running and the pending re-armed job
(`squeue --me --name=viz_watcher`). **Restart:** `sbatch viz_watcher_daily.sh`.

---

## Web data format (the manifest contract)

`postprocess_airquality.py` writes, per cycle, into `web_out/<cycle>/`:

- `manifest.json` — grid size, lon/lat bbox, per-hour valid times, species metadata, daily-product metadata.
- `pm25_<cycle>.bin`, `o3_<cycle>.bin` — uint16 packed hourly grids (little-endian, hour-major, rows N→S; `value = u/scale`, `65535` = nodata).
- `daily_<cycle>.bin` — per local day: 24-hr PM2.5 + MDA8 ozone, same packing.
- `pm25_<cycle>.tif`, `o3_<cycle>.tif` — multiband Cloud-Optimized GeoTIFFs (GIS downloads / future tile server).
- `forecast_<cycle>.html` — standalone shareable viewer with this cycle embedded.

The viewer reads `manifest.json` + the `.bin` files; it computes AQI client-side
from concentrations, so color scales stay adjustable without reprocessing.

---

## Troubleshooting

- **`SyntaxError` on `-> str` / numeric literals** — the wrong Python ran. The
  `aqf` conda env wasn't active; confirm `python --version` is 3.11.
- **`conda: command not found`** — load `anaconda3` first; the scripts do this
  themselves, so run them with `bash`, don't pre-activate.
- **`Permission denied` running `./script.sh`** — `/data` is `noexec`; use `bash script.sh`.
- **New cycle not showing on the site** — browser cached `manifest.json`. Hard-refresh
  once (Ctrl/Cmd+Shift+R); the `no-cache` header prevents it thereafter. Verify with
  `…/manifest.json?v=N` (should show the new `"cycle"`).
- **Publish fails from a compute node** — must be the `meng` partition (others have
  no internet). Login nodes also have internet but cannot run `cron`.
- **Watcher stopped re-arming** — its job hit the `--time` limit mid-loop before the
  re-arm line. Ensure `--time` ≥ `MAX_POLLS × INTERVAL`.

---

## Status & future work

**Done:** hourly + daily views, AQI/concentration coloring (AIRPACT banded PM2.5
scale), click-to-query, state/county overlays, nightly auto-publish to Cloudflare,
live AirNow monitor observations (Pages Function `/api/obs`, 10-min edge cache;
needs the `AIRNOW_API_KEY` secret — greyed out gracefully when unavailable, e.g.
in local previews and standalone embeds), past-forecast archive (every cycle in
`web_out/` is published under `data/<cycle>/` with a `data/cycles.json` index;
the viewer's "Forecast cycle" selector loads any archived cycle), gzipped `.bin`
transfer (publisher stages `.bin.gz`, viewer inflates with pako, falls back to raw
`.bin` for local previews), staleness banner (viewer warns on the map if the
latest cycle is >36 h old — a silent watcher/publish failure no longer presents
a stale forecast as current), 72-hour sparkline click popups (AQI band shading,
hover-to-inspect, animation-synced cursor, AirNow obs point on monitor popups,
daily-AQI bars in Daily view).

**Forecast verification** (`/verify.html`): every night `verify_airnow.py` pairs
yesterday's AirNow hourly obs with the archived forecasts covering that day
(lead day 1–3), stores sufficient statistics under `web_out/verification/`
(per-day history + rolling 90-day `summary.json`), and the page computes
bias/RMSE/correlation client-side with species/window/region/lead filters.
Runs inside `postprocess_and_publish.sh` (non-fatal on failure); backfillable
with `--backfill YYYYMMDD`.

**Ideas:**
- Fire overlays (HMS smoke polygons, NASA FIRMS hotspots), tribal lands, Class I areas.
- "Use my location" button + place search.
- Shareable permalinks (cycle/species/hour/view in the URL hash).
- Eventual mirror to `airpact.wsu.edu` — the same `web_out` artifacts drop straight in.

---

## Contacts

- Visualization pipeline: Jun Meng
- Operational AIRPACT-6 forecast pipeline: Priom Zarrah
