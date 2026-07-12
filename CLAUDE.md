# Memory — AIRPACT6 Visualization

## Me
Jun Meng (jun.meng@dal.ca). Owns the AIRPACT-6 visualization pipeline + viewer.
Edits on Mac (`~/work/AIRPACT/Visualization`), runs pipeline on WSU Kamiak HPC as `jun.meng`.

## People
| Who | Role |
|-----|------|
| **Jun** | Jun Meng — this pipeline, viewer, Cloudflare account |
| **Priom** | Priom Zarrah — operational AIRPACT-6 forecast (CMAQ); we only READ his `AP6_outputs` |

## Terms
| Term | Meaning |
|------|---------|
| **cycle** | one daily forecast run, named `YYYYMMDD`, 72 hourly steps |
| **the viewer** | `pipeline/pnw-air-forecast.html`, deployed as site index.html |
| **the site** | https://nw-air-forecast.pages.dev/ (Cloudflare Pages, project `nw-air-forecast`) |
| **web_out** | `/data/project/airpact/jmeng/Visualization/web_out/<cycle>/` on Kamiak — per-cycle artifacts |
| **the watcher** | `viz_watcher_daily.sh`, self-rearming SLURM job, 07:00 daily |
| **obs** | AirNow observations, via Pages Function `/api/obs` |
| **aqf** | conda env on Kamiak (python 3.11 + geo stack + nodejs + wrangler) |

## Workflow (critical)
1. Edit + commit on Mac → `git push` (repo: github.com/Jun-Meng/airpact6_InteractiveViewer)
2. Kamiak login node: `git pull` in `/data/project/airpact/jmeng/Visualization`
3. `bash publish_cloudflare.sh` (login node or `meng` partition — only ones with internet)
4. Hard-refresh site (Cmd+Shift+R) to bypass cached manifest

GitHub push does NOT deploy — deploy is always wrangler from Kamiak.

## Gotchas (learned the hard way)
- Never `git commit --amend` after pushing → non-fast-forward mess (happened 2026-07-07)
- Kamiak interactive shell: `source "$(conda info --base)/etc/profile.d/conda.sh"` before `conda activate` (don't `conda init`)
- Run scripts as `bash script.sh` — `/data` is noexec
- Claude's sandbox can commit in the mounted folder but not push (no creds) and leaves stale `.git/*.lock` files — remove them before local git commands
- Secrets: `wrangler pages secret put NAME` — NAME stays literal, key is pasted at the prompt
- AirNow `/aq/data`: `dataType=B` returns BOTH concentration (`Value`) and AQI; -999 = missing

## Status (as of end of 2026-07-11)
Worklogs: 2026-07-07/08 (detail), 2026-07-10 (consolidated + engineering rules), **2026-07-11 (READ FIRST — final interface model + latest)**.
- ALL shipped + confirmed through commit 5f93c4e (incl. late-07-10 UI consolidation). Full detail in 2026-07-10.md.
- Viewer now has: obs layer w/ dynamic dots + obs curves, sparkline popups, archive, verification (3 metrics incl. MDA8 + exceedances) + per-site badges, HMS smoke + clustered VIIRS fires, Class I/tribal/grid overlays, collapsible overlay panel, topo basemap, permalinks, find-a-place, Pacific-time UI, failure emails.
- UI layout (FINAL, post-matrix): sidebar = Find a place · "What to show" LAYER MATRIX (5 products, replaced pollutant seg + view toggle) · Forecast cycle · Overlays; map chip (collapsible) = playback + readout + site-marker key; legend card (collapsible) = title + AQI/Conc + opacity + scale; hover = values, click = pinnable 72-h popups (draggable comparison cards).
- MapLibre marker rules (bit us 07-11): never overwrite className, never CSS-transform marker elements — classList + clip-path.
- Queue: Phase 2 GOES-R FDC FRP (Kamiak job, task #25); monthly statistics (~Aug 1).
- Engineering rules that bit us (details in 2026-07-10.md): AirNow ≤24 h queries; never JSON.parse big bodies in Workers (stream+peek); CF cache survives deploys; test with EXACT prod params; popups are dark-themed.
