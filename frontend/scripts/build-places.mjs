// Builds the Philippine address lists the school forms suggest from:
//
//   public/places/provinces.json        every province, its region, and its
//                                       cities and municipalities
//   public/places/barangays/<code>.json the barangays of one province, by city
//
// The source is the PSA's Philippine Standard Geographic Code, through the
// psgc.gitlab.io API. The lists change rarely, so this is run by hand when
// the PSA publishes an update, and the output is committed:
//
//   node scripts/build-places.mjs
import { mkdir, rm, writeFile } from 'node:fs/promises';

const API = 'https://psgc.gitlab.io/api';
const OUT = new URL('../public/places/', import.meta.url);

// Metro Manila has no province; it is offered as one so its cities can be
// picked the same way.
const METRO_MANILA = '130000000';
// Two independent cities sit outside any province in the code; they are
// listed under the province they are in.
const HOME_PROVINCE = { 'City of Isabela': '150700000', 'City of Cotabato': '153800000' };

async function get(path) {
  const res = await fetch(`${API}/${path}/`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

const byName = (a, b) => a.name.localeCompare(b.name);
// The code writes "Lanao Del Norte"; the province calls itself "Lanao del Norte".
const tidy = (name) => name.replace(/ (Del|De|Ng) /g, (_, word) => ` ${word.toLowerCase()} `);

const [regions, provinces, cities, barangays] = await Promise.all([
  get('regions'),
  get('provinces'),
  get('cities-municipalities'),
  get('barangays'),
]);

const regionLabel = Object.fromEntries(
  regions.map((r) => [
    r.code,
    r.regionName.startsWith('Region ') ? `${r.regionName} (${r.name})` : r.name,
  ]),
);

const homeOf = (city) =>
  city.provinceCode || HOME_PROVINCE[city.name] || (city.regionCode === METRO_MANILA ? METRO_MANILA : null);

const list = [
  ...provinces,
  { code: METRO_MANILA, name: 'Metro Manila', regionCode: METRO_MANILA },
]
  .sort(byName)
  .map((p) => ({
    code: p.code,
    name: tidy(p.name),
    region: regionLabel[p.regionCode],
    cities: cities
      .filter((c) => homeOf(c) === p.code)
      .sort(byName)
      .map((c) => [c.code, c.name]),
  }));

const cityProvince = Object.fromEntries(cities.map((c) => [c.code, homeOf(c)]));
const perProvince = {};
for (const b of barangays) {
  const city = b.cityCode || b.municipalityCode;
  const province = cityProvince[city];
  if (!province) continue;
  ((perProvince[province] ??= {})[city] ??= []).push(b.name);
}

await rm(OUT, { recursive: true, force: true });
await mkdir(new URL('barangays/', OUT), { recursive: true });
await writeFile(new URL('provinces.json', OUT), JSON.stringify(list));
for (const [province, byCity] of Object.entries(perProvince)) {
  // A few cities have two barangays of the same name; one entry is enough.
  for (const [city, names] of Object.entries(byCity)) {
    byCity[city] = [...new Set(names)].sort((a, b) => a.localeCompare(b));
  }
  await writeFile(new URL(`barangays/${province}.json`, OUT), JSON.stringify(byCity));
}

const missing = cities.filter((c) => !homeOf(c)).map((c) => c.name);
console.log(
  `${list.length} provinces, ${cities.length} cities and municipalities, ` +
    `${barangays.length} barangays.` +
    (missing.length ? ` Not placed: ${missing.join(', ')}` : ''),
);
