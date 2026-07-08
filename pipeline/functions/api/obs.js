/* GET /api/obs — latest AirNow PM2.5 + ozone observations for the AIRPACT domain.
 *
 * Cloudflare Pages Function. Proxies airnowapi.org so the API key stays
 * server-side (secret AIRNOW_API_KEY), and edge-caches the reply for 10 min
 * so site traffic can never exhaust AirNow's 500 req/hr limit.
 *
 * Reply: { fetched: ISO, sites: [ { name, agency, lon, lat,
 *            pm?: {v, aqi, unit, utc}, o3?: {v, aqi, unit, utc} } ] }
 */

const BBOX = "-125.92,39.79,-109.59,49.84"; // AIRPACT domain (lon/lat)
const TTL = 600;                            // edge-cache seconds

export async function onRequestGet(context) {
  const key = context.env.AIRNOW_API_KEY;
  if (!key) return json({ error: "AIRNOW_API_KEY not configured" }, 503);

  const cache = caches.default;
  const cacheKey = new Request(new URL("/api/obs", context.request.url));
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  // last 3 h -> newest record per site (AirNow posts with ~1-2 h lag)
  const now = new Date();
  const fmt = (d) => d.toISOString().slice(0, 13);   // YYYY-MM-DDTHH (UTC)
  const url =
    "https://www.airnowapi.org/aq/data/" +
    `?startDate=${fmt(new Date(now.getTime() - 3 * 3600e3))}&endDate=${fmt(now)}` +
    "&parameters=PM25,OZONE&dataType=B&format=application/json" +
    "&verbose=1&monitorType=0&includerawconcentrations=0" +
    `&BBOX=${BBOX}&API_KEY=${key}`;

  const r = await fetch(url);
  if (!r.ok) return json({ error: `AirNow HTTP ${r.status}` }, 502);
  const rows = await r.json();
  if (!Array.isArray(rows)) return json({ error: "unexpected AirNow payload" }, 502);

  // newest record per site + parameter; -999 = AirNow missing-data sentinel
  const sites = new Map();
  for (const rec of rows) {
    const p = rec.Parameter === "PM2.5" ? "pm" : rec.Parameter === "OZONE" ? "o3" : null;
    if (!p || rec.Value == null || rec.Value < -900) continue;
    const id = rec.FullAQSCode || `${rec.Latitude},${rec.Longitude}`;
    const s = sites.get(id) || {
      id, name: rec.SiteName, agency: rec.AgencyName,
      lon: rec.Longitude, lat: rec.Latitude,
    };
    if (!s[p] || rec.UTC > s[p].utc)
      s[p] = { v: rec.Value, aqi: rec.AQI, unit: rec.Unit, utc: rec.UTC };
    sites.set(id, s);
  }

  const resp = json(
    { fetched: now.toISOString(), sites: [...sites.values()] },
    200,
    { "Cache-Control": `public, max-age=${TTL}` }
  );
  context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}

function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}
