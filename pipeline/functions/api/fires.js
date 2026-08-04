/* GET /api/fires — VIIRS active-fire detections (last 24 h) for the AIRPACT domain.
 *
 * Proxies NASA FIRMS (Ultra Real-Time for the US: detections appear within
 * ~1 min of overpass). Merges all three VIIRS satellites, converts CSV to
 * GeoJSON, edge-caches 30 min. Key: free FIRMS map key stored as the
 * FIRMS_MAP_KEY Pages secret (limit 5000 transactions / 10 min — we use 3/30 min).
 *
 * Reply: GeoJSON FeatureCollection + top-level "updated"; per-feature props:
 *   frp (MW), sat, when (UTC), conf (l/n/h), dn (D/N)
 */

const BBOX = "-125.92,39.79,-109.59,49.84";  // west,south,east,north
const SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"];
const SAT = { N: "S-NPP", "1": "NOAA-20", "2": "NOAA-21",
              G16: "GOES-16", G17: "GOES-17", G18: "GOES-18", G19: "GOES-19" };
const MAX_AGE_H = 24;
const TTL = 1800;
// ?src=goes -> "burning now": GOES redetects the same pixel every 5-10 min,
// so we keep a short window and deduplicate to the newest detection per
// ~2 km cell. Shorter cache: freshness is this layer's whole point.
const GOES_SRC = "GOES_NRT", GOES_AGE_H = 2, GOES_TTL = 600;

export async function onRequestGet(context) {
  const key = context.env.FIRMS_MAP_KEY;
  if (!key) return json({ error: "FIRMS_MAP_KEY not configured" }, 503);

  const isGoes = new URL(context.request.url).searchParams.get("src") === "goes";
  const cache = caches.default;
  const cacheKey = new Request(new URL(isGoes ? "/api/fires?src=goes" : "/api/fires",
                                       context.request.url));
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  const now = Date.now();
  const maxAgeH = isGoes ? GOES_AGE_H : MAX_AGE_H;
  let features = [];
  let okSources = 0;
  for (const src of (isGoes ? [GOES_SRC] : SOURCES)) {
    try {
      // day range 2 so the window is always covered regardless of UTC hour
      const r = await fetch(`https://firms.modaps.eosdis.nasa.gov/api/area/csv/${key}/${src}/${BBOX}/2`);
      if (!r.ok) continue;
      const text = await r.text();
      if (/^invalid/i.test(text.trim())) continue;  // FIRMS reports errors as 200 text
      features.push(...csvPoints(text, now, maxAgeH));
      okSources++;
    } catch (e) { /* tolerate a single satellite feed being down */ }
  }
  if (!okSources) return json({ error: "all FIRMS sources failed" }, 502);

  if (isGoes) { // newest detection per ~2 km cell
    const best = new Map();
    for (const f of features) {
      const [lon, lat] = f.geometry.coordinates;
      const k = Math.round(lat / 0.02) + "," + Math.round(lon / 0.02);
      const prev = best.get(k);
      if (!prev || f.properties.when > prev.properties.when) best.set(k, f);
    }
    features = [...best.values()];
  }

  const fc = { type: "FeatureCollection",
               updated: new Date(now).toISOString().slice(0, 16) + "Z",
               count: features.length, features };
  const resp = new Response(JSON.stringify(fc), {
    status: 200,
    headers: { "Content-Type": "application/json",
               "Cache-Control": `public, max-age=${isGoes ? GOES_TTL : TTL}` },
  });
  context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}

function csvPoints(text, now, maxAgeH = MAX_AGE_H) {
  const lines = text.trim().split("\n");
  if (lines.length < 2) return [];
  const head = lines[0].split(",");
  const ix = (n) => head.indexOf(n);
  const iLat = ix("latitude"), iLon = ix("longitude"), iDate = ix("acq_date"),
        iTime = ix("acq_time"), iFrp = ix("frp"), iSat = ix("satellite"),
        iConf = ix("confidence"), iDN = ix("daynight");
  if (iLat < 0 || iLon < 0 || iDate < 0 || iTime < 0) return [];
  const out = [];
  for (let k = 1; k < lines.length; k++) {
    const c = lines[k].split(",");
    if (c.length < head.length) continue;
    const hhmm = String(c[iTime]).padStart(4, "0");
    const t = Date.parse(`${c[iDate]}T${hhmm.slice(0, 2)}:${hhmm.slice(2)}:00Z`);
    if (!Number.isFinite(t) || now - t > maxAgeH * 3600e3) continue;
    out.push({ type: "Feature",
      geometry: { type: "Point", coordinates: [+c[iLon], +c[iLat]] },
      properties: { frp: +c[iFrp] || 0, sat: SAT[c[iSat]] || c[iSat],
                    when: new Date(t).toISOString().slice(0, 16) + "Z",
                    conf: iConf >= 0 ? c[iConf] : "", dn: iDN >= 0 ? c[iDN] : "" } });
  }
  return out;
}

function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), {
    status, headers: { "Content-Type": "application/json", ...headers } });
}
