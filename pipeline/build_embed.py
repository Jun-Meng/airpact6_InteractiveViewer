#!/usr/bin/env python3
"""
build_embed.py — bake one forecast cycle into a single shareable HTML.

Takes the viewer template (pnw-air-forecast.html) plus a web_out/<cycle>
data folder produced by postprocess_airquality.py, and writes a standalone
file with the data embedded (downsampled + gzip + base64). The result needs
no data server — only an internet connection for the basemap tiles and the
two JS libraries (MapLibre, pako) it loads from a CDN.

Usage:
    python build_embed.py --data web_out/20260628
    python build_embed.py --data web_out/20260628 --html pnw-air-forecast.html \
                          --out forecast_20260628.html --factor 2

--factor is spatial downsampling for the embedded copy only (2 => 8 km cells,
~3 MB file). Use 1 for full 4 km resolution (larger file).

Requires: numpy  (already in the 'aqf' conda env).
"""
import argparse, base64, gzip, json, os, re, warnings
import numpy as np

MAPLIBRE_RE = re.compile(r'(<script src="https://[^"]*maplibre-gl[^"]*\.min\.js"></script>)')
PAKO_TAG = '<script src="https://cdnjs.cloudflare.com/ajax/libs/pako/2.1.0/pako.min.js"></script>'


def block_mean(stack_u16, nbands, H, W, factor, scale):
    """Downsample a (nbands*H*W) uint16 buffer by factor, preserving nodata."""
    a = stack_u16.reshape(nbands, H, W).astype(np.float32)
    a[a == 65535] = np.nan
    a /= scale
    H2, W2 = H // factor, W // factor
    a = a[:, :H2 * factor, :W2 * factor].reshape(nbands, H2, factor, W2, factor)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = np.nanmean(a, axis=(2, 4))
    q = np.where(np.isfinite(m), np.clip(np.round(m * scale), 0, 65534), 65535).astype("<u2")
    return q, H2, W2


def pack(path, nbands, scale, H, W, factor):
    u = np.fromfile(path, dtype="<u2")
    q, H2, W2 = block_mean(u, nbands, H, W, factor, scale)
    return base64.b64encode(gzip.compress(q.tobytes(), 9)).decode(), H2, W2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="web_out/<cycle> folder with manifest.json + .bin files")
    ap.add_argument("--html", default="pnw-air-forecast.html", help="viewer template")
    ap.add_argument("--out", default=None, help="output file (default forecast_<cycle>.html)")
    ap.add_argument("--factor", type=int, default=2, help="spatial downsample for the embed (default 2)")
    args = ap.parse_args()

    mf = json.load(open(os.path.join(args.data, "manifest.json")))
    W, H = mf["grid"]["width"], mf["grid"]["height"]
    f = max(1, args.factor)

    def binpath(name): return os.path.join(args.data, name)

    nH = len(mf["hours"])
    pm_b64, H2, W2 = pack(binpath(mf["species"]["pm25"]["bin"]), nH, mf["species"]["pm25"]["scale"], H, W, f)
    o3_b64, _, _ = pack(binpath(mf["species"]["o3"]["bin"]), nH, mf["species"]["o3"]["scale"], H, W, f)

    embed = {"pm": pm_b64, "o3": o3_b64}
    if mf.get("daily") and os.path.exists(binpath(mf["daily"]["bin"])):
        nD = len(mf["daily"]["days"])
        embed["daily"], _, _ = pack(binpath(mf["daily"]["bin"]), nD * 2, mf["daily"]["pm_scale"], H, W, f)

    # downsized grid in the embedded manifest; file refs no longer needed
    mf["grid"] = {"width": W2, "height": H2}
    embed["manifest"] = mf
    blob = json.dumps(embed).replace("</", "<\\/")

    template = open(args.html).read()
    if not MAPLIBRE_RE.search(template):
        raise SystemExit("Could not find the MapLibre <script> tag in the template HTML.")
    inject = (("" if PAKO_TAG in template else PAKO_TAG + "\n")  # viewer may already load pako
              + '<script id="aqdata" type="application/json">' + blob + '</script>\n'
              + '<script>window.__AQ_EMBED__=JSON.parse(document.getElementById("aqdata").textContent);</script>\n')
    html = MAPLIBRE_RE.sub(lambda m: m.group(1) + "\n" + inject, template, count=1)

    out = args.out or f"forecast_{mf['cycle']}.html"
    open(out, "w").write(html)
    mb = os.path.getsize(out) / 1e6
    print(f"cycle {mf['cycle']}: embedded {W2}x{H2} grid (from {W}x{H}, /{f}), {nH} hours")
    print(f"  -> {out}  ({mb:.2f} MB)")


if __name__ == "__main__":
    main()
