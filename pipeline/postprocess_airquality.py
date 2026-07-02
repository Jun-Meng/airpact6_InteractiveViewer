#!/usr/bin/env python3
"""
postprocess_airquality.py
=========================
Convert daily CMAQ (IOAPI) surface forecast output into lightweight web map
artifacts for the Northwest Air Forecast viewer.

Runs on Kamiak as a post-processing step right after the forecast finishes.
For each species it:
  1. reads the variable + grid definition straight from the file attributes
  2. decodes TFLAG into UTC + local valid times
  3. converts units to display units (ozone ppmV -> ppb)
  4. warps the Lambert Conformal grid to Web Mercator (EPSG:3857) so a web
     map can place it as an axis-aligned overlay
  5. writes a packed uint16 binary (the browser loads this) and a multi-band
     Cloud-Optimized GeoTIFF (GIS download / future tile server)
  6. writes manifest.json tying it all together

Outputs are pushed to the public web host afterwards (rsync/scp); nothing here
needs to run as a server.

Usage
-----
    python postprocess_airquality.py \
        --cycle 20260628 \
        --pm25 /path/PM25_AELMO_20260628.nc \
        --o3   /path/O3_ACONC_20260628.nc \
        --outdir ./web_out

Dependencies: numpy netCDF4 pyproj rasterio
"""
from __future__ import annotations
import argparse, json, os
from datetime import datetime, timedelta, timezone

import numpy as np
from netCDF4 import Dataset
from pyproj import Transformer
from rasterio.transform import Affine, array_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling


def _resolve_local_tz(name="America/Los_Angeles"):
    """Prefer stdlib zoneinfo (Py>=3.9); fall back to pytz; last resort a fixed
    PDT offset. A real tz database (zoneinfo/pytz) is needed for correct PST/PDT."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        pass
    try:
        import pytz
        return pytz.timezone(name)
    except Exception:
        pass
    return timezone(timedelta(hours=-7))


# IOAPI uses a perfect sphere; CMAQ default radius. Change here if your setup differs.
IOAPI_SPHERE_R = 6370000.0
LOCAL_TZ = _resolve_local_tz()
DST_CRS = "EPSG:3857"

# species config: which variable to read and how to present it.
# 'scale' is the fixed-point factor for the uint16 packing (value * scale).
SPECIES = {
    "pm25": dict(var="PM25", display_units="ug/m3", scale=10.0),
    "o3":   dict(var="O3",   display_units="ppb",   scale=10.0),
}
U16_NODATA = 65535


def lcc_proj4(a) -> str:
    """Build a PROJ string for an IOAPI GDTYP=2 (Lambert Conformal) grid."""
    return (f"+proj=lcc +lat_1={a['P_ALP']} +lat_2={a['P_BET']} "
            f"+lat_0={a['YCENT']} +lon_0={a['P_GAM']} +x_0=0 +y_0=0 "
            f"+R={IOAPI_SPHERE_R} +units=m +no_defs")


def read_attrs(nc) -> dict:
    keys = ["GDTYP", "P_ALP", "P_BET", "P_GAM", "XCENT", "YCENT",
            "XORIG", "YORIG", "XCELL", "YCELL", "NCOLS", "NROWS",
            "SDATE", "STIME", "TSTEP"]
    a = {k: nc.getncattr(k) for k in keys if k in nc.ncattrs()}
    if a.get("GDTYP") != 2:
        raise ValueError(f"Only GDTYP=2 (LCC) supported here; got {a.get('GDTYP')}")
    return a


def decode_times(nc, a):
    """Return list of UTC datetimes, one per timestep, from TFLAG (preferred)
    or reconstructed from SDATE/STIME/TSTEP."""
    out = []
    if "TFLAG" in nc.variables:
        tf = nc.variables["TFLAG"][:, 0, :]  # (TSTEP, 2): YYYYDDD, HHMMSS
        for ymd, hms in tf:
            ymd, hms = int(ymd), int(hms)
            year, doy = divmod(ymd, 1000)
            hh, rem = divmod(hms, 10000)
            mm, ss = divmod(rem, 100)
            dt = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(
                days=doy - 1, hours=hh, minutes=mm, seconds=ss)
            out.append(dt)
    else:
        ymd, hms, step = int(a["SDATE"]), int(a["STIME"]), int(a["TSTEP"])
        year, doy = divmod(ymd, 1000)
        base = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(
            days=doy - 1, hours=hms // 10000)
        n = nc.dimensions["TSTEP"].size
        dh = step // 10000
        out = [base + timedelta(hours=dh * i) for i in range(n)]
    return out


def to_ppb(arr, units):
    u = units.strip().lower()
    if "ppm" in u:                 # CMAQ ACONC gases are ppmV
        return arr * 1000.0
    return arr                      # already ppb / ug m-3


def process_species(paths, cfg):
    """Accepts one path or a list of daily files; concatenates them into a
    single contiguous time series (sorted by valid time, duplicates dropped)."""
    if isinstance(paths, str):
        paths = [paths]
    attrs = None
    chunks = []  # (times_list, data_array)
    for p in paths:
        nc = Dataset(p)
        a = read_attrs(nc)
        if attrs is None:
            attrs = a
        var = nc.variables[cfg["var"]]
        d = np.asarray(var[:, 0, :, :], np.float32)        # (TSTEP, ROW, COL)
        if cfg["display_units"] == "ppb":
            d = to_ppb(d, var.units)
        chunks.append((decode_times(nc, a), d))
        nc.close()

    chunks.sort(key=lambda c: c[0][0])
    times, frames, seen = [], [], set()
    for ts, d in chunks:
        for k, t in enumerate(ts):
            if t in seen:
                continue
            seen.add(t); times.append(t); frames.append(d[k])
    data = np.stack(frames)
    a = attrs

    nt, nrows, ncols = data.shape
    xcell, ycell = float(a["XCELL"]), float(a["YCELL"])
    xorig, yorig = float(a["XORIG"]), float(a["YORIG"])

    # IOAPI ROW index increases northward; rasterio wants north-up (row0=north).
    data = data[:, ::-1, :]
    src_crs = lcc_proj4(a)
    src_transform = Affine(xcell, 0, xorig, 0, -ycell, yorig + nrows * ycell)
    left, bottom = xorig, yorig
    right, top = xorig + ncols * xcell, yorig + nrows * ycell

    dst_transform, dst_w, dst_h = calculate_default_transform(
        src_crs, DST_CRS, ncols, nrows, left, bottom, right, top)

    warped = np.full((nt, dst_h, dst_w), np.nan, np.float32)
    for t in range(nt):
        reproject(
            source=data[t], destination=warped[t],
            src_transform=src_transform, src_crs=src_crs,
            dst_transform=dst_transform, dst_crs=DST_CRS,
            src_nodata=np.nan, dst_nodata=np.nan,
            resampling=Resampling.bilinear)

    # geographic bbox of the (axis-aligned mercator) output
    w_m, s_m, e_m, n_m = array_bounds(dst_h, dst_w, dst_transform)
    to4326 = Transformer.from_crs(DST_CRS, "EPSG:4326", always_xy=True)
    west, south = to4326.transform(w_m, s_m)
    east, north = to4326.transform(e_m, n_m)

    return dict(times=times, warped=warped, dst_transform=dst_transform,
                width=dst_w, height=dst_h, bbox=[west, south, east, north],
                vmin=float(np.nanmin(warped)), vmax=float(np.nanmax(warped)))


def write_binary(path, warped, scale):
    """Pack to little-endian uint16: hour-major, rows north->south, cols west->east.
    NaN -> 65535 sentinel."""
    q = np.where(np.isfinite(warped),
                 np.clip(np.round(warped * scale), 0, U16_NODATA - 1),
                 U16_NODATA).astype("<u2")
    q.tofile(path)
    return os.path.getsize(path)


def write_cog(path, warped, dst_transform, scale_meta):
    """Multi-band COG (band per hour) in EPSG:3857 for downloads / tile server."""
    import rasterio
    nt, h, w = warped.shape
    profile = dict(driver="GTiff", height=h, width=w, count=nt, dtype="float32",
                   crs=DST_CRS, transform=dst_transform, nodata=float("nan"),
                   tiled=True, blockxsize=256, blockysize=256,
                   compress="deflate", predictor=3)
    try:
        profile["driver"] = "COG"
        profile.pop("tiled"); profile.pop("blockxsize"); profile.pop("blockysize")
        with rasterio.open(path, "w", **profile) as dst:
            for t in range(nt):
                dst.write(warped[t], t + 1)
                dst.set_band_description(t + 1, scale_meta[t])
    except Exception:
        profile["driver"] = "GTiff"
        profile.update(tiled=True, blockxsize=256, blockysize=256)
        with rasterio.open(path, "w", **profile) as dst:
            for t in range(nt):
                dst.write(warped[t], t + 1)
                dst.set_band_description(t + 1, scale_meta[t])
    return os.path.getsize(path)


# ---- daily AQI products ------------------------------------------------------
# PM2.5 daily AQI uses the 24-hour mean (>=16 valid hours to count).
# Ozone daily AQI uses MDA8: the max of the 8-hour running means whose window
# starts between 07:00 and 23:00 local time (EPA convention).
PM_MIN_HOURS = 16
O3_MIN_WINDOWS = 6


def compute_daily(times, pm_warped, o3_warped):
    import warnings
    nt, H, W = pm_warped.shape
    local = [t.astimezone(LOCAL_TZ) for t in times]

    # 8-hour running means of ozone over the whole series; window labeled by start
    avg8 = np.full((nt, H, W), np.nan, np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        for h in range(nt - 7):
            avg8[h] = np.nanmean(o3_warped[h:h + 8], axis=0)

    groups = {}
    for i, lt in enumerate(local):
        groups.setdefault(lt.date(), []).append(i)

    days = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        for d, idx in groups.items():
            pm_ok = len(idx) >= PM_MIN_HOURS
            pm24 = np.nanmean(pm_warped[idx], axis=0) if pm_ok else None
            win = [i for i in idx if 7 <= local[i].hour <= 23 and i <= nt - 8]
            o3_ok = len(win) >= O3_MIN_WINDOWS
            mda8 = np.nanmax(np.stack([avg8[i] for i in win]), axis=0) if win else None
            days.append(dict(date=d, n_hours=len(idx), n_windows=len(win),
                             pm_ok=pm_ok, o3_ok=o3_ok, pm24=pm24, mda8=mda8))
    return days


def write_daily_bin(path, days, scale_pm, scale_o3, H, W):
    """Per qualifying day: [pm24*scale_pm, mda8*scale_o3] as uint16 LE,
    day-major then [pm,o3], rows N->S. Missing metric -> all nodata."""
    blank = np.full((H, W), U16_NODATA, "<u2")

    def pack(arr, scale):
        if arr is None:
            return blank
        return np.where(np.isfinite(arr),
                        np.clip(np.round(arr * scale), 0, U16_NODATA - 1),
                        U16_NODATA).astype("<u2")

    with open(path, "wb") as f:
        for day in days:
            pack(day["pm24"], scale_pm).tofile(f)
            pack(day["mda8"], scale_o3).tofile(f)
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", required=True, help="forecast cycle, e.g. 20260628")
    ap.add_argument("--pm25", required=True, nargs="+", help="one or more PM2.5 files")
    ap.add_argument("--o3", required=True, nargs="+", help="one or more O3 files")
    ap.add_argument("--outdir", default="./web_out")
    ap.add_argument("--no-cog", action="store_true", help="skip GeoTIFF/COG output")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    paths = {"pm25": args.pm25, "o3": args.o3}
    results = {}
    manifest = {"cycle": args.cycle,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "crs": "EPSG:3857", "tz": "America/Los_Angeles",
                "binary_layout": "uint16 LE, hour-major, rows N->S, cols W->E; value=u/scale; 65535=nodata",
                "species": {}, "hours": None, "bbox": None, "grid": None}

    for sp, cfg in SPECIES.items():
        r = process_species(paths[sp], cfg)
        results[sp] = r
        bin_name = f"{sp}_{args.cycle}.bin"
        size = write_binary(os.path.join(args.outdir, bin_name), r["warped"], cfg["scale"])

        files = {"bin": bin_name, "bin_bytes": size}
        if not args.no_cog:
            labels = [t.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M %Z") for t in r["times"]]
            cog_name = f"{sp}_{args.cycle}.tif"
            files["cog"] = cog_name
            files["cog_bytes"] = write_cog(os.path.join(args.outdir, cog_name),
                                           r["warped"], r["dst_transform"], labels)

        manifest["species"][sp] = dict(
            var=cfg["var"], units=cfg["display_units"], scale=cfg["scale"],
            nodata=U16_NODATA, vmin=round(r["vmin"], 2), vmax=round(r["vmax"], 2),
            **files)

        # grid/time metadata is shared; set once from the first species
        if manifest["hours"] is None:
            manifest["bbox"] = [round(v, 5) for v in r["bbox"]]
            manifest["grid"] = {"width": r["width"], "height": r["height"]}
            manifest["init_time"] = dict(
                utc=r["times"][0].isoformat(),
                local=r["times"][0].astimezone(LOCAL_TZ).isoformat())
            manifest["hours"] = [
                dict(i=i, utc=t.isoformat(),
                     local=t.astimezone(LOCAL_TZ).isoformat(),
                     label=t.astimezone(LOCAL_TZ).strftime("%a %H:%M"))
                for i, t in enumerate(r["times"])]

    # ---- daily AQI products (24-hr PM2.5 + MDA8 ozone, per local day) ----
    times = results["pm25"]["times"]
    H, W = manifest["grid"]["height"], manifest["grid"]["width"]
    days = compute_daily(times, results["pm25"]["warped"], results["o3"]["warped"])
    qualifying = [d for d in days if d["pm_ok"] or d["o3_ok"]]
    daily_name = f"daily_{args.cycle}.bin"
    write_daily_bin(os.path.join(args.outdir, daily_name), qualifying,
                    SPECIES["pm25"]["scale"], SPECIES["o3"]["scale"], H, W)
    manifest["daily"] = dict(
        bin=daily_name,
        layout="uint16 LE, day-major then [pm24, o3mda8], rows N->S; value=u/scale; 65535=nodata",
        pm_scale=SPECIES["pm25"]["scale"], o3_scale=SPECIES["o3"]["scale"],
        nodata=U16_NODATA,
        days=[dict(date=d["date"].isoformat(),
                   label=d["date"].strftime("%a %b %-d"),
                   pm_valid=d["pm_ok"], o3_valid=d["o3_ok"],
                   n_hours=d["n_hours"], n_o3_windows=d["n_windows"])
              for d in qualifying])

    with open(os.path.join(args.outdir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    g = manifest["grid"]
    print(f"cycle {args.cycle}: {len(manifest['hours'])} hours, "
          f"grid {g['width']}x{g['height']} (3857)")
    print(f"  bbox lon/lat: {manifest['bbox']}")
    for sp, m in manifest["species"].items():
        line = f"  {sp:5s} range {m['vmin']}–{m['vmax']} {m['units']}  bin {m['bin_bytes']/1e6:.2f} MB"
        if "cog_bytes" in m:
            line += f"  cog {m['cog_bytes']/1e6:.2f} MB"
        print(line)
    print(f"  daily: {len(qualifying)} day(s) -> {daily_name}")
    for d in manifest["daily"]["days"]:
        print(f"    {d['label']}: PM {'ok' if d['pm_valid'] else 'n/a'} "
              f"({d['n_hours']}h), O3 {'ok' if d['o3_valid'] else 'n/a'} "
              f"({d['n_o3_windows']} windows)")


if __name__ == "__main__":
    main()
