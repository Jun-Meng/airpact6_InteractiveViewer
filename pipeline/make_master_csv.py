#!/usr/bin/env python3
"""
make_master_csv.py — flatten verification/series/*.json into one master CSV.

Each series file holds a site's full-history day-1 hourly pairs (AirNow obs +
AIRPACT-6 forecast) as written by verify_airnow.py. This emits the same
numbers as the viewer popup's "CSV" button, but for ALL sites in one file.

Usage (Kamiak, any python3):
  python make_master_csv.py                          # PM2.5, full history
  python make_master_csv.py --species o3
  python make_master_csv.py --start 2026-07-01 --end 2026-08-31 \
      --out ap6_pm25_master_JulAug.csv

Columns: site_id, site_name, lon, lat, time_pt, time_utc,
         airpact6_hourly, airnow_hourly   (units: ug/m3 for pm, ppb for o3)
Rows where BOTH values are missing are skipped.
"""

import argparse, csv, json, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

STAGE_ROOT = Path("/data/project/airpact/jmeng/Visualization/web_out")
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--species", choices=("pm", "o3"), default="pm")
    ap.add_argument("--start", help="first local day, YYYY-MM-DD")
    ap.add_argument("--end", help="last local day, YYYY-MM-DD")
    ap.add_argument("--stage-root", default=str(STAGE_ROOT))
    ap.add_argument("--out", help="output CSV (default: ap6_<species>_master.csv)")
    args = ap.parse_args()

    sdir = Path(args.stage_root) / "verification" / "series"
    files = sorted(sdir.glob("*.json"))
    if not files:
        sys.exit(f"no series files under {sdir}")

    t_min = date.fromisoformat(args.start) if args.start else None
    t_max = date.fromisoformat(args.end) if args.end else None
    out_path = Path(args.out or f"ap6_{args.species}_master.csv")

    n_rows, n_sites = 0, 0
    with out_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["site_id", "site_name", "lon", "lat", "time_pt", "time_utc",
                    "airpact6_hourly", "airnow_hourly"])
        for f in files:
            d = json.loads(f.read_text())
            rec = d.get(args.species)
            if not rec:
                continue
            sid = f.stem
            name = d.get("name", sid)
            lon, lat = d.get("lon"), d.get("lat")
            # hour k = start-day local midnight + k hours (verify_airnow.py)
            t0 = datetime.combine(date.fromisoformat(d["start"]),
                                  datetime.min.time(), LOCAL_TZ)
            wrote = False
            for k in range(d["hours"]):
                o, fc = rec["o"][k], rec["f"][k]
                if o is None and fc is None:
                    continue
                t = t0 + timedelta(hours=k)
                if t_min and t.date() < t_min:
                    continue
                if t_max and t.date() > t_max:
                    continue
                w.writerow([sid, name, lon, lat,
                            t.strftime("%Y-%m-%dT%H:%M"),
                            t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                            "" if fc is None else fc,
                            "" if o is None else o])
                n_rows += 1
                wrote = True
            n_sites += wrote

    print(f"{out_path}: {n_rows} rows, {n_sites} sites, species={args.species}")


if __name__ == "__main__":
    main()
