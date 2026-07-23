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

import argparse, json, math, os, re, sys, time, urllib.request, urllib.parse
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


def _fetch_range(t_start, t_end, key):
    """One AirNow /aq/data call. KEEP SPANS <= ~24 h — the API 502s on domain-wide
    queries much larger than a day (learned the hard way, twice)."""
    url = ("https://www.airnowapi.org/aq/data/?"
           + urllib.parse.urlencode({
               "startDate": t_start.strftime("%Y-%m-%dT%H"),
               "endDate": t_end.strftime("%Y-%m-%dT%H"),
               "parameters": "PM25,OZONE", "BBOX": BBOX,
               "dataType": "B", "format": "application/json",
               "verbose": "1", "monitorType": "0",
               "includerawconcentrations": "0", "API_KEY": key}))
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                rows = json.load(r)
            if not isinstance(rows, list):
                raise RuntimeError(f"unexpected AirNow payload: {str(rows)[:200]}")
            return rows
        except Exception as e:            # transient 502s are common; back off and retry
            last = e
            time.sleep(10 * (attempt + 1))
    raise last


def fetch_obs(day, key):
    """Hourly AirNow rows for local day `day` plus 7 h spillover, fetched as
    two <=24 h chunks (MDA8 windows starting 17:00-23:00 need the spillover)."""
    t0 = datetime.combine(day, datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc)
    rows = _fetch_range(t0, t0 + timedelta(hours=23), key)
    rows += _fetch_range(t0 + timedelta(hours=24), t0 + timedelta(hours=30), key)
    return rows, t0


def obs_mda8(series, t0):
    """Observed MDA8 (ppb): max 8-h mean over windows starting 07:00-23:00
    local (offsets 7-23 from local midnight t0), >= 6 valid hours per window.
    Mirrors the model's MDA8 definition in postprocess_airquality.py."""
    best = None
    for w in range(7, 24):
        vals = [series.get(w + i) for i in range(8)]
        vals = [v for v in vals if v is not None]
        if len(vals) >= 6:
            m = sum(vals) / len(vals)
            if best is None or m > best:
                best = m
    return best


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

    daily = None
    if mf.get("daily") and (d / mf["daily"]["bin"]).is_file():
        db = np.fromfile(d / mf["daily"]["bin"], dtype="<u2").reshape(-1, H, W)
        daily = {"arr": db, "o3_scale": mf["daily"]["o3_scale"],
                 "nodata": mf["daily"]["nodata"],
                 "dates": [dd["date"][:10] for dd in mf["daily"]["days"]]}

    def model_mda8(date_iso, lon, lat):
        """Model MDA8 ozone (ppb) for local date `date_iso` at a point, or None."""
        if not daily or date_iso not in daily["dates"]:
            return None
        c = cell(lon, lat)
        if c is None:
            return None
        u = int(daily["arr"][daily["dates"].index(date_iso) * 2 + 1, c[1], c[0]])
        return None if u == daily["nodata"] else u / daily["o3_scale"]

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

    return {"hours": hour_idx, "value": value, "mda8": model_mda8}


def verify_day(day, key):
    """Build per-day stats for local day `day` (a date)."""
    d8 = day.strftime("%Y%m%d")
    rows, t0 = fetch_obs(day, key)
    day_end = t0 + timedelta(hours=24)
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
    o3_series = {}          # sid -> {hour offset from local midnight: ppb}
    ser = {}                # sid -> sp -> {"o":[24], "f":[24]} day-1 paired series
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
        t = datetime.fromisoformat(r["UTC"][:16]).replace(tzinfo=timezone.utc)
        if sp == "o3":  # collect hourly series (incl. 7 h spillover) for obs MDA8
            o3_series.setdefault(sid, {})[round((t - t0).total_seconds() / 3600)] = float(v)
        if t >= day_end:
            continue    # spillover hours feed MDA8 only, not the hourly stats
        utc = r["UTC"][:13] + ":00"
        # site-history obs: record independently of model availability (the
        # cycle starts 01:00 PT, so midnight would otherwise be a fake gap)
        if 1 in cycles:
            slot = int((t - t0).total_seconds() // 3600)
            if 0 <= slot < 24:
                e = ser.setdefault(sid, {}).setdefault(
                    sp, {"o": [None] * 24, "f": [None] * 24})
                e["o"][slot] = round(float(v), 1)
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

    # day-1 model series: fill independently of obs gaps so the stitched
    # history line stays continuous (only for sites that reported anything)
    if 1 in cycles:
        cyc1 = cycles[1]
        hours_utc = [(t0 + timedelta(hours=k)).astimezone(timezone.utc)
                     .strftime("%Y-%m-%dT%H:00") for k in range(24)]
        for sid, s in sites.items():
            for sp in ("pm", "o3"):
                e = ser.setdefault(sid, {}).setdefault(
                    sp, {"o": [None] * 24, "f": [None] * 24})
                for slot, hu in enumerate(hours_utc):
                    hidx = cyc1["hours"].get(hu)
                    if hidx is None:
                        continue
                    f = cyc1["value"](sp, hidx, s["lon"], s["lat"])
                    if f is not None:
                        e["f"][slot] = round(f, 1)

    # MDA8 ozone: one pair per site per lead day (regulatory daily metric)
    date_iso, n_m8 = day.isoformat(), 0
    for sid, series in o3_series.items():
        o = obs_mda8(series, t0)
        if o is None:
            continue
        s = sites[sid]
        for ld, cyc in cycles.items():
            f = cyc["mda8"](date_iso, s["lon"], s["lat"])
            if f is None:
                continue
            a = rec.setdefault((sid, "m8", ld), [0, 0.0, 0.0, 0.0, 0.0, 0.0])
            a[0] += 1; a[1] += o; a[2] += f
            a[3] += o * o; a[4] += f * f; a[5] += o * f
            n_m8 += 1

    print(f"  {d8}: {n_pairs} hourly + {n_m8} MDA8 pairs, {len(sites)} sites, leads {sorted(cycles)}")
    rnd = lambda x: round(x, 3)
    return {
        "date": d8,
        "sites": sites,
        "rec": [[sid, sp, ld] + [a[0]] + [rnd(x) for x in a[1:]]
                for (sid, sp, ld), a in sorted(rec.items())],
        "lh": [[sp, h] + [a[0]] + [rnd(x) for x in a[1:]]
               for (sp, h), a in sorted(lh.items())],
        "ser": ser,
    }


def rebuild_summary(vdir, window):
    hist = sorted((vdir / "history").glob("[0-9]" * 8 + ".json"))[-window:]
    sites, days, ser_days = {}, [], []
    for p in hist:
        d = json.loads(p.read_text())
        sites.update(d["sites"])
        days.append({"date": d["date"], "rec": d["rec"], "lh": d["lh"]})
        if d.get("ser"):  # kept OUT of summary.json (too big); series writer only
            ser_days.append({"date": d["date"], "ser": d["ser"]})
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = {"updated": now_iso, "window_days": window, "sites": sites, "days": days}
    (vdir / "summary.json").write_text(json.dumps(out, separators=(",", ":")))
    n = sum(len(d["rec"]) for d in days)
    print(f"summary.json: {len(days)} days, {len(sites)} sites, {n} site-day records, "
          f"{(vdir / 'summary.json').stat().st_size / 1e6:.2f} MB")

    # small per-site skill digest for the viewer's popup badges.
    # Hourly metrics (pm, o3): DAY-1 pairs only (lead day 1 = the first 24
    # forecast hours) from the last DAY1_CYCLES verified cycles — "how has
    # today's-type forecast done here lately". MDA8 keeps the 30-day all-lead
    # pool (1 pair/site/day; a 3-cycle window would be n=3, meaningless).
    DAY1_CYCLES = 3
    agg = {}
    for d in days[-DAY1_CYCLES:]:
        for r in d["rec"]:
            if r[1] in ("pm", "o3") and r[2] == 1:
                a = agg.setdefault((r[0], r[1]), [0, 0.0, 0.0, 0.0, 0.0, 0.0])
                for i in range(6):
                    a[i] += r[3 + i]
    digest_days = days[-30:]
    for d in digest_days:
        for r in d["rec"]:
            if r[1] == "m8":
                a = agg.setdefault((r[0], "m8"), [0, 0.0, 0.0, 0.0, 0.0, 0.0])
                for i in range(6):
                    a[i] += r[3 + i]
    dsites = {}
    for (sid, sp), a in agg.items():
        n2, so, sf, soo, sff, sof = a
        if not n2:
            continue
        dsites.setdefault(sid, {})[sp] = {
            "b": round((sf - so) / n2, 2),
            "r": round(math.sqrt(max(0.0, (sff - 2 * sof + soo) / n2)), 2),
            "n": n2}
    (vdir / "sites.json").write_text(json.dumps(
        {"updated": now_iso, "window_days": min(30, len(digest_days)),
         "day1_cycles": min(DAY1_CYCLES, len(days)),
         "sites": dsites}, separators=(",", ":")))
    print(f"sites.json: skill digest for {len(dsites)} sites, "
          f"{(vdir / 'sites.json').stat().st_size / 1e3:.0f} KB")

    # per-site FULL-HISTORY day-1 series for the viewer's site-history panel:
    # verification/series/<sid>.json, hourly obs + day-1 model, hour k =
    # start-day local midnight + k hours (missing days stay null).
    write_site_series(vdir, ser_days, sites)


def write_site_series(vdir, days, sites_meta):
    from datetime import date as _date
    sdays = [d for d in days if d.get("ser")]
    if not sdays:
        return
    d0 = _date.fromisoformat(f"{sdays[0]['date'][:4]}-{sdays[0]['date'][4:6]}-{sdays[0]['date'][6:]}")
    d1 = _date.fromisoformat(f"{sdays[-1]['date'][:4]}-{sdays[-1]['date'][4:6]}-{sdays[-1]['date'][6:]}")
    nh = ((d1 - d0).days + 1) * 24
    per = {}
    for d in sdays:
        dd = _date.fromisoformat(f"{d['date'][:4]}-{d['date'][4:6]}-{d['date'][6:]}")
        off = (dd - d0).days * 24
        for sid, sps in d["ser"].items():
            rec = per.setdefault(sid, {})
            for sp, e in sps.items():
                a = rec.setdefault(sp, {"o": [None] * nh, "f": [None] * nh})
                a["o"][off:off + 24] = e["o"]
                a["f"][off:off + 24] = e["f"]
    sdir = vdir / "series"
    sdir.mkdir(exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n_files = 0
    for sid, rec in per.items():
        # drop species with no data at all
        rec = {sp: a for sp, a in rec.items()
               if any(v is not None for v in a["o"]) or any(v is not None for v in a["f"])}
        if not rec:
            continue
        meta = sites_meta.get(sid, {})
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sid))
        (sdir / f"{safe}.json").write_text(json.dumps(
            {"updated": now_iso, "start": d0.isoformat(), "hours": nh,
             "tz": "America/Los_Angeles", "name": meta.get("name", sid),
             "lon": meta.get("lon"), "lat": meta.get("lat"), **rec},
            separators=(",", ":")))
        n_files += 1
    print(f"series/: {n_files} site files, {len(sdays)} day(s), {nh} hourly slots each")
    write_hourly_day1(vdir, sdays, sites_meta)


def write_hourly_day1(vdir, ser_days, sites_meta, n_days=14):
    """verification/hourly-day1.json: flat (obs, fc) day-1 hourly pairs from
    the trailing n_days, with a per-pair state index — feeds the TRUE hourly
    scatter on verify.html (summary.json only has sufficient statistics)."""
    days = ser_days[-n_days:]
    states = sorted({m.get("st", "OTH") for m in sites_meta.values()} | {"OTH"})
    sidx = {s: i for i, s in enumerate(states)}
    out = {}
    for sp in ("pm", "o3"):
        o_arr, f_arr, st_arr = [], [], []
        for d in days:
            for sid, sps in d["ser"].items():
                e = sps.get(sp)
                if not e:
                    continue
                st = sidx[sites_meta.get(sid, {}).get("st", "OTH")] \
                    if sites_meta.get(sid, {}).get("st", "OTH") in sidx else 0
                for o, f in zip(e["o"], e["f"]):
                    if o is None or f is None:
                        continue
                    o_arr.append(o); f_arr.append(f); st_arr.append(st)
        out[sp] = {"o": o_arr, "f": f_arr, "si": st_arr}
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (vdir / "hourly-day1.json").write_text(json.dumps(
        {"updated": now_iso, "days": len(days), "states": states, **out},
        separators=(",", ":")))
    print(f"hourly-day1.json: {len(out['pm']['o'])} pm + {len(out['o3']['o'])} o3 "
          f"day-1 hourly pairs over {len(days)} day(s)")


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
