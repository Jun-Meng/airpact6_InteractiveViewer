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

## Status
Worklogs: `memory/worklog/2026-07-07.md`, `2026-07-08.md` (full detail there).
- Deployed + confirmed live: obs layer, conc default, archive selector, gzip, staleness banner, sparklines with obs curve (chunked ≤24 h obs-series fetches — AirNow dies on bigger windows)
- Verification (`/verify.html` + nightly `verify_airnow.py`): set up 07-08 — confirm it shows data next session
- Built, NOT deployed: permalinks (hash state) + find-a-place (Nominatim search + geolocate) — Jun to push/pull/republish, then test per worklog
- Agreed next: HMS smoke + FIRMS fire overlay; then verification badge in popups, publish-failure email, MDA8
- Key API gotchas: AirNow /aq/data max ~24 h per domain-wide query; CF edge cache survives deploys (id-matching fallback exists)
