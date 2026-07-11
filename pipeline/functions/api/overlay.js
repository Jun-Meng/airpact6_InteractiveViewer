/* GET /api/overlay?name=tribal|class1
 *
 * Reference-boundary overlays for the viewer, proxied from official ArcGIS
 * services (clipped to the AIRPACT domain, server-side simplified) and
 * edge-cached 7 days — these boundaries essentially never change.
 *
 *  tribal : Census TIGERweb "Federal American Indian Reservations" (layer 2)
 *  class1 : EPA OAQPS "Mandatory Class 1 Federal Areas" (Living Atlas, public)
 */

const COMMON = {
  geometry: "-125.92,39.79,-109.59,49.84",
  geometryType: "esriGeometryEnvelope",
  inSR: "4326", outSR: "4326",
  spatialRel: "esriSpatialRelIntersects",
  where: "1=1",
  maxAllowableOffset: "0.02",   // ~2 km simplification, plenty at viewer zooms
  geometryPrecision: "4",
  returnGeometry: "true",
  f: "geojson",
};
const SOURCES = {
  tribal: {
    base: "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/AIANNHA/MapServer/2/query",
    fields: "NAME", ttl: 7 * 86400,
  },
  class1: {
    base: "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/Mandatory_Class1_Federal_Areas/FeatureServer/0/query",
    fields: "NAME,STATE", ttl: 7 * 86400,
  },
  // NOAA NESDIS SAB HMS smoke analysis (Living Atlas mirror, CC0). Today's
  // analyst-drawn plumes, updated through the sunlit day -> short cache.
  smoke: {
    base: "https://services2.arcgis.com/C8EMgrsFcRFL6LrL/arcgis/rest/services/NOAA_Satellite_Smoke_Detection_(v1)/FeatureServer/0/query",
    fields: "Density,Satellite,Start,End_", ttl: 1200,
  },
  // Census TIGERweb primary roads. Layer 1 is the PRE-GENERALIZED 2.5M-650k
  // scale version — layer 2 (full detail) is tens of MB domain-wide and kills
  // the Worker parsing it (same lesson as the >24 h AirNow queries).
  roads: {
    base: "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer/1/query",
    fields: "FULLNAME", ttl: 7 * 86400, params: { maxAllowableOffset: "0.005" },
  },
};

export async function onRequestGet(context) {
  const name = new URL(context.request.url).searchParams.get("name");
  const src = SOURCES[name];
  if (!src) return json({ error: "name must be tribal, class1, smoke, or roads" }, 400);

  const cache = caches.default;
  const cacheKey = new Request(new URL(`/api/overlay?name=${name}`, context.request.url));
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  const url = src.base + "?" + new URLSearchParams({ ...COMMON, outFields: src.fields, ...(src.params || {}) });
  const r = await fetch(url);
  if (!r.ok) return json({ error: `upstream HTTP ${r.status}` }, 502);

  // STREAM the body through — never JSON.parse here: multi-MB payloads (roads)
  // exceed the Worker CPU budget. Validate by peeking at the first bytes only
  // (ArcGIS errors are 200s starting {"error":...; real data starts with
  // {"type":"FeatureCollection").
  const [peek, body] = r.body.tee();
  const reader = peek.getReader();
  const first = await reader.read();
  reader.cancel();
  const head = new TextDecoder().decode(
    first.value ? first.value.slice(0, 300) : new Uint8Array());
  if (!head.includes('"FeatureCollection"'))
    return json({ error: "unexpected upstream payload" }, 502);

  const resp = new Response(body, {
    status: 200,
    headers: { "Content-Type": "application/json",
               "Cache-Control": `public, max-age=${src.ttl}` },
  });
  context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}

function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), {
    status, headers: { "Content-Type": "application/json", ...headers } });
}
