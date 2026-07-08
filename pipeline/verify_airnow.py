#!/usr/bin/env python3
"""
verify_airnow.py — pair AirNow observations with archived AIRPACT-6 forecasts.

For a given LOCAL day (America/Los_Angeles), fetches that day's hourly AirNow
observations (PM2.5 + ozone) for the AIRPACT domain, extracts the forecast
value at each monitor site from the archived cycles that cover the day
(lead day 1 = same-day cycle, 2 = previous day's, 3 = two days back), and
accumulates sufficient statistics (n, Σo, Σf, Σo², Σf², Σof) so the web page
can compute bias / RMSE / correlation for any filter combination client-side.

Outputs (under <stage_root>/verification/):
  history/<YYYYMMDD>.json   per-day stats (kept forever, cheap)
  summary.json              rolling merge of the last --window days
                            (published to the site as data/verification/summary.json)

Usage (Kamiak, aqf env):
  python verify_airnow.py --date 20260707          # one local day
  python verify_airnow.py --backfill 20260703      # 20260703 .. yesterday
  python verify_airnow.py                          # default: yesterday

API key: env AIRNOW_API_KEY, or parsed from ~/.airnow_env
         (line: export AIRNOW_API_KEY=...). Free key: https://docs.airnowapi.org
"""

import argparse, json, math, os, re, sys, urllib.request, urllib.parse
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

STAGE_ROOT = Path("/data/project/airpact/jmeng/Visualization/web_out")
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
BBOX = "-125.92,39.79,-109.59,49.84"
WINDOW_DAYS = 90          # days merged into the published summary.json
LEADS = (1, 2, 3)         # lead day -> cycle = day - (lead-1)
FIPS = {"53": "WA", "41": "OR", "16": "ID", "30": "MT", "32": "NV",
        "06": "CA", "56": "WY", "49": "UT"}


def api_key():
    k = os.environ.get("AIRNOW_API_KEY")
    if not k:
        env = Path.home() / ".airnow_env"
        if env.is_file():
            m = re.search(r"AIRNOW_API_KEY=([A-Za-z0-9-]+)", env.read_text())
            if m:
                k = m.group(1)
    if not k:
        sys.exit("no AIRNOW_API_KEY (env var or ~/.airnow_env)")
    return k


def fetch_obs(day, key):
    """Hourly AirNow rows covering local day `day` (UTC window, inclusive start)."""
    t0 = datetime.combine(day, datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc)
    t1 = t0 + timedelta(hours=23)
    url = ("https://www.airnowapi.org/aq/data/?"
           + urllib.parse.urlencode({
               "startDate": t0.strftime("%Y-%m-%dT%H"),
               "endDate": t1.strftime("%Y-%m-%dT%H"),
               "parameters": "PM25,OZONE", "BBOX": BBOX,
               "dataType": "B", "format": "application/json",
               "verbose": "1", "monitorType": "0",
               "includerawconcentrations": "0", "API_KEY": key}))
    with urllib.request.urlopen(url, timeout=120) as r:
        rows = json.load(r)
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected AirNow payload: {str(rows)[:200]}")
    return rows


def load_cycle(cycle):
    """Archived cycle -> dict with manifest, hour map (utc iso -> index), value fn."""
    d = STAGE_ROOT / cycle
    mf_path = d / "manifest.json"
    if not mf_path.is_file():
        return None
    mf = json.loads(mf_path.read_text())
    W, H = mf["grid"]["width"], mf["grid"]["height"]
    west, south, east, north = mf["bbox"]

    def merc(lat):
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    mn, ms = merc(north), merc(south)

    def cell(lon, lat):
        i = int((lon - west) / (east - west) * W)
        j = int((mn - merc(lat)) / (mn - ms) * H)
        return (i, j) if 0 <= i < W and 0 <= j < H else None

    arrs = {}
    for sp, key in (("pm", "pm25"), ("o3", "o3")):
        meta = mf["species"][key]
        a = np.fromfile(d / meta["bin"], dtype="<u2").reshape(-1, H, W)
        arrs[sp] = (a, meta["scale"], meta["nodata"])

    hour_idx = {}
    for h in mf["hours"]:
        iso = datetime.fromisoformat(h["utc"]).astimezone(timezone.utc)
        hour_idx[iso.strftime("%Y-%m-%dT%H:00")] = h["i"]

    def value(sp, hidx, lon, lat):
        c = cell(lon, lat)
        if c is None:
            return None
        a, scale, nodata = arrs[sp]
        u = int(a[hidx, c[1], c[0]])
        return None if u == nodata else u / scale

    return {"hours": hour_idx, "value": value}


def verify_day(day, key):
    """Build per-day stats for local day `day` (a date)."""
    d8 = day.strftime("%Y%m%d")
    rows = fetch_obs(day, key)
    cycles = {}
    for ld in LEADS:
        c = (day - timedelta(days=ld - 1)).strftime("%Y%m%d")
        cyc = load_cycle(c)
        if cyc:
            cycles[ld] = cyc
    if not cycles:
        print(f"  {d8}: no archived cycles cover this day — skipped")
        return None

    sites, rec, lh = {}, {}, {}
    n_pairs = 0
    for r in rows:
        sp = {"PM2.5": "pm", "OZONE": "o3"}.get(r.get("Parameter"))
        v = r.get("Value")
        if sp is None or v is None or v < -900:
            continue
        sid = r.get("FullAQSCode") or f"{r['Latitude']},{r['Longitude']}"
        if sid not in sites:
            st = FIPS.get(str(sid)[:2], "OTH") if str(sid)[:2].isdigit() else "OTH"
            sites[sid] = {"name": r.get("SiteName", sid), "lon": r["Longitude"],
                          "lat": r["Latitude"], "st": st}
        utc = r["UTC"][:13] + ":00"
        for ld, cyc in cycles.items():
            hidx = cyc["hours"].get(utc)
            if hidx is None:
                continue
            f = cyc["value"](sp, hidx, r["Longitude"], r["Latitude"])
            if f is None:
                continue
            o = float(v)
            k = (sid, sp, ld)
            a = rec.setdefault(k, [0, 0.0, 0.0, 0.0, 0.0, 0.0])
            b = lh.setdefault((sp, hidx), [0, 0.0, 0.0, 0.0, 0.0, 0.0])
            for acc in (a, b):
                acc[0] += 1; acc[1] += o; acc[2] += f
                acc[3] += o * o; acc[4] += f * f; acc[5] += o * f
            n_pairs += 1

    print(f"  {d8}: {n_pairs} pairs, {len(sites)} sites, leads {sorted(cycles)}")
    rnd = lambda x: round(x, 3)
    return {
        "date": d8,
        "sites": sites,
        "rec": [[sid, sp, ld] + [a[0]] + [rnd(x) for x in a[1:]]
                for (sid, sp, ld), a in sorted(rec.items())],
        "lh": [[sp, h] + [a[0]] + [rnd(x) for x in a[1:]]
               for (sp, h), a in sorted(lh.items())],
    }


def rebuild_summary(vdir, window):
    hist = sorted((vdir / "history").glob("[0-9]" * 8 + ".json"))[-window:]
    sites, days = {}, []
    for p in hist:
        d = json.loads(p.read_text())
        sites.update(d["sites"])
        days.append({"date": d["date"], "rec": d["rec"], "lh": d["lh"]})
    out = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "window_days": window, "sites": sites, "days": days}
    (vdir / "summary.json").write_text(json.dumps(out, separators=(",", ":")))
    n = sum(len(d["rec"]) for d in days)
    print(f"summary.json: {len(days)} days, {len(sites)} sites, {n} site-day records, "
          f"{(vdir / 'summary.json').stat().st_size / 1e6:.2f} MB")


def main():
    global STAGE_ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="local day YYYYMMDD (default: yesterday)")
    ap.add_argument("--backfill", help="verify every day from this YYYYMMDD through yesterday")
    ap.add_argument("--stage-root", default=str(STAGE_ROOT))
    ap.add_argument("--window", type=int, default=WINDOW_DAYS)
    args = ap.parse_args()

    STAGE_ROOT = Path(args.stage_root)
    vdir = STAGE_ROOT / "verification"
    (vdir / "history").mkdir(parents=True, exist_ok=True)

    yesterday = datetime.now(LOCAL_TZ).date() - timedelta(days=1)
    if args.backfill:
        start = datetime.strptime(args.backfill, "%Y%m%d").date()
        days = [start + timedelta(days=i) for i in range((yesterday - start).days + 1)]
    else:
        days = [datetime.strptime(args.date, "%Y%m%d").date() if args.date else yesterday]

    key = api_key()
    for day in days:
        out = verify_day(day, key)
        if out:
            (vdir / "history" / f"{out['date']}.json").write_text(
                json.dumps(out, separators=(",", ":")))
    rebuild_summary(vdir, args.window)


if __name__ == "__main__":
    main()
