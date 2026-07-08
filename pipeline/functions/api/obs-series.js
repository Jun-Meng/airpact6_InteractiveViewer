/* GET /api/obs-series?start=YYYY-MM-DDTHH&end=YYYY-MM-DDTHH (UTC hours)
 *
 * Hourly AirNow observations for the AIRPACT domain over one forecast window,
 * used by the viewer to draw the observed curve in click popups. One upstream
 * AirNow call per distinct window per cache period: 10 min while the window
 * includes the present, 6 h once it is entirely in the past (obs final).
 *
 * Reply: { start, hours, sites: { id: { name, lon, lat,
 *            pm?: [v|null per hour], o3?: [v|null per hour] } } }
 * id = FullAQSCode (fallback "lat,lon") — matches /api/obs site ids.
 */

const BBOX = "-125.92,39.79,-109.59,49.84";
const MAX_HOURS = 78;

export async function onRequestGet(context) {
  const key = context.env.AIRNOW_API_KEY;
  if (!key) return json({ error: "AIRNOW_API_KEY not configured" }, 503);

  const q = new URL(context.request.url).searchParams;
  const start = parseHour(q.get("start")), end = parseHour(q.get("end"));
  if (!start || !end || end <= start || (end - start) / 3600e3 > MAX_HOURS)
    return json({ error: "bad or missing start/end (UTC YYYY-MM-DDTHH, span <= 78 h)" }, 400);

  const cache = caches.default;
  const cacheKey = new Request(
    new URL(`/api/obs-series?start=${isoH(start)}&end=${isoH(end)}`, context.request.url));
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  const url = "https://www.airnowapi.org/aq/data/?" + new URLSearchParams({
    startDate: isoH(start), endDate: isoH(end),
    parameters: "PM25,OZONE", BBOX,
    dataType: "B", format: "application/json", verbose: "1",
    monitorType: "0", includerawconcentrations: "0", API_KEY: key });
  const r = await fetch(url);
  if (!r.ok) return json({ error: `AirNow HTTP ${r.status}` }, 502);
  const rows = await r.json();
  if (!Array.isArray(rows)) return json({ error: "unexpected AirNow payload" }, 502);

  const nH = Math.round((end - start) / 3600e3) + 1;
  const sites = {};
  for (const rec of rows) {
    const p = rec.Parameter === "PM2.5" ? "pm" : rec.Parameter === "OZONE" ? "o3" : null;
    if (!p || rec.Value == null || rec.Value < -900) continue;
    const idx = Math.round((Date.parse(rec.UTC + ":00Z") - start) / 3600e3);
    if (idx < 0 || idx >= nH) continue;
    const id = rec.FullAQSCode || `${rec.Latitude},${rec.Longitude}`;
    const s = sites[id] || (sites[id] = { name: rec.SiteName, lon: rec.Longitude, lat: rec.Latitude });
    (s[p] || (s[p] = new Array(nH).fill(null)))[idx] = Math.round(rec.Value * 10) / 10;
  }

  const pastOnly = end < Date.now() - 2 * 3600e3;
  const resp = json({ start: isoH(start), hours: nH, sites }, 200,
    { "Cache-Control": `public, max-age=${pastOnly ? 21600 : 600}` });
  context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}

function parseHour(s) {
  if (!s || !/^\d{4}-\d{2}-\d{2}T\d{2}$/.test(s)) return null;
  const t = Date.parse(s + ":00:00Z");
  return Number.isFinite(t) ? t : null;
}
function isoH(t) { return new Date(t).toISOString().slice(0, 13); }
function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), {
    status, headers: { "Content-Type": "application/json", ...headers } });
}
