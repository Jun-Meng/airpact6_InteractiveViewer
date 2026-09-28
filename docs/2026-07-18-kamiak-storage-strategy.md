# Kamiak AIRPACT-6 Data Storage Strategy

Draft 2026-07-18 · Jun Meng · for discussion with Priom
Trigger: 07-18 incident — lab space hit 100%, CMAQ wedged (couldn't write logs), forecast delayed; watcher job also blocked CMAQ on the `meng` partition.

## 1. Where we are (from the 07-18 email chain)

| Item | Number |
|---|---|
| 3D CONC output | **57 GB/day**, ×3-day cycle ≈ 171 GB written per cycle; ~5–6 TB/month if all kept |
| Priom's lab space | ~12 TB held (mostly monthly model output; 3D kept only for Bluesky + 2 July fix versions); **1.1 TB free** after cleanup ≈ 10–15 days |
| AP lab space | **710 GB free** ≈ 10 days |
| Smoke output + daily logs | land in lab space, grow continuously — this is what actually wedged CMAQ |
| Temp fix | June 3D moved to `/scratch/user/a.zarrah/conc_20260718_111652` |
| My web_out (`/data/project/airpact/jmeng`) | ~11 MB/cycle bins + COGs + embedded HTML ≈ 50–100 MB/cycle, few GB/month, unlimited retention (minor, but same policy vacuum) |

**Scratch warning:** Kamiak scratch workspaces expire at **14 days max** (default = max = `14-00:00`), then auto-delete. "Until Aug 1" is the hard wall — anything in that June folder we still want must be analyzed or copied out **before ~Jul 28**.

Kamiak storage facts (hpc.wsu.edu): faculty lab folder 500 GB free; additional lab storage rentable annually via Service Desk; archive tier exists but minimum 10 TB/yr **plus** a 10 TB/yr lab co-rental. Scratch is free but 2-week TTL.

## 2. Core insight: we don't need to keep everything at full resolution

Three levers, roughly multiplicative:

1. **Day-1 only.** Each calendar day is covered by 3 overlapping cycles; for retrospective fire/plume analysis, day-1 (first 24 h of each cycle) is the best analysis. Keeping day-1 only: 5–6 TB/mo → **~1.7 TB/mo**.
2. **Compression.** 3D CONC compresses well: NetCDF-4 deflate (`nccopy -d 4` or `ncks -4 -L 4`) typically 2–3×; add short-int packing for ~2× more if lossy is acceptable. 1.7 TB → **~0.6–0.9 TB/mo**.
3. **Variable subsetting.** If the use case is fires + plume rise, keep smoke-relevant species + a few diagnostics (all layers), drop the rest of the mechanism. Often another 3–5×.

Combined: a month of "everything we'd realistically look at" fits in **a few hundred GB — inside the existing free space**, no purchase needed.

Fallback lever: keep the *inputs* (emissions + met for interesting episodes) and rerun CMAQ on demand instead of storing 3D output. Storage ≈ free; cost = compute + Priom's time. Good for the long tail, not for same-month lookups.

## 3. Retention tiers (proposed)

| Tier | What | Where | Retention |
|---|---|---|---|
| A | 2D surface (PM25_only, O3_only), web_out artifacts, verification history | /data project space | 90+ days (site mirrors the public archive anyway) |
| B1 | 3D CONC, raw, full | lab space | rolling **3 days** (current cycle + safety), then delete or demote |
| B2 | 3D day-1, compressed, subset ("fire archive") | /data project space | rolling **60–90 days** during fire season (Jul–Oct); off-season: don't produce |
| C | Logs, smoke intermediates, other run scratch | lab space | rotate **7–14 days**, auto-delete |

## 4. Automation (the part that prevents recurrence)

- **Housekeeping SLURM job** (same self-rearming pattern as my watcher): daily, after the forecast chain — `find -mtime +N -delete` per tier, build the B2 archive file (subset → deflate → move to /data), then `du` report.
- **Pre-run disk guard** at the top of the CMAQ chain: if free space < ~250 GB (≈ 1.5 cycles), abort with an email instead of wedging mid-run. A stuck-writing-logs failure is the worst kind — it looks like a hang.
- **Monitoring:** fold a daily free-space line into the existing failure-email system; amber alert at, say, <500 GB.
- **Scheduling fix:** watcher currently collides with CMAQ on `meng`. Either `sbatch --dependency=afterok:<cmaq_chain>` for the watcher, or move watcher to a shared partition / later trigger keyed on cycle completion rather than clock time.
- **web_out:** add `KEEP_DAYS=120` cleanup to the publisher or housekeeping job (Kamiak-side only; the site archive is untouched — #39 SITE_KEEP_DAYS remains a separate, already-approved decision).

## 5. Phased plan

**Now → Jul 28**
1. Decide fate of June 3D on scratch before the 14-day purge: analyze, or copy the needed subset (compressed) to /data.
2. Add the pre-run disk guard + log rotation (tier C). Cheap, kills the failure mode.
3. Fix watcher/CMAQ scheduling collision.

**By ~Aug 8 (before free space runs out again)**
4. Agree with Priom on B2 spec: variable list, layers, day-1-only, compression level. Measure actual compressed size on one cycle first.
5. Stand up the housekeeping job with tiers A–C.

**Fall (evaluate, don't pre-buy)**
6. If B2 at 90 days still pressures space, options in cost order: (a) tighten subset, (b) Cloudflare R2 via rclone from a login node (~$0.015/GB-mo, no egress fees, account already exists — dovetails with the viewer's phase-2 R2 archive), (c) Kamiak rental (10 TB/yr archive + 10 TB lab co-rental minimum — only worth it if we truly need ~20 TB warm).

## 6. Open questions for Priom

1. Exact variable list needed for fire/plume-rise analysis (and whether LAY subsetting is acceptable).
2. What "monthly simulated model output" in the 12 TB is still needed vs. deletable/compressible — biggest immediate reclaim.
3. Can smoke daily output + logs move to a rotated directory now?
4. Compressed size of one day-1 subset file (pilot on today's cycle) — sets the real B2 budget.

## Appendix: measurement commands (run on Kamiak)

```bash
# per-directory usage, biggest first
du -sh /data/lab/<labspace>/* 2>/dev/null | sort -rh | head -20
du -sh /data/project/airpact/* 2>/dev/null | sort -rh | head -20
du -sh /data/project/airpact/jmeng/Visualization/web_out/* | tail -5

# free space
df -h /data/lab/<labspace> /data/project/airpact

# scratch workspaces + expiry
lsworkspace

# pilot compression test on one 3D file
ml anaconda3 && conda activate aqf
ncks -4 -L 4 CCTM_CONC_<cycle>_day1.nc conc_test_deflate.nc && ls -lh conc_test_deflate.nc
```
