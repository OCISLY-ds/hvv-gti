"use strict";
// Ausfuehren: node --test tests/js/style.test.js
// Prueft die Hintergrundkarte ohne Schluessel (OpenFreeMap) gegen die Ebenen des OpenMapTiles-Schemas.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const src = fs.readFileSync(path.join(__dirname, "../../map/static/app.js"), "utf8");
const part = src.slice(src.indexOf("// Hintergrundkarte ohne Schluessel"), src.indexOf("async function loadJson"));
const style = vm.runInNewContext(`const GLYPHS = "g/{fontstack}/{range}", OSM_ATTR = "osm";\n${part}\nopenFreeMapStyle()`, {});

// Ebenen des OpenMapTiles-Schemas laut TileJSON von https://tiles.openfreemap.org/planet
const SCHEMA = new Set(["aerodrome_label", "aeroway", "boundary", "building", "housenumber", "landcover", "landuse", "mountain_peak", "park", "place", "poi", "transportation", "transportation_name", "water", "water_name", "waterway"]);

test("the style needs no key and uses only layers that exist in the tiles", () => {
  assert.equal(style.version, 8);
  assert.equal(style.sources.ofm.url, "https://tiles.openfreemap.org/planet");
  assert.ok(!JSON.stringify(style).includes("carto"));
  for (const layer of style.layers) if (layer["source-layer"]) assert.ok(SCHEMA.has(layer["source-layer"]), layer["source-layer"]);
});

test("layer ids are unique and every layer has a source", () => {
  const ids = style.layers.map((l) => l.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const layer of style.layers) assert.ok(layer.type === "background" || layer.source in style.sources, layer.id);
});

test("roads without rails or footpaths are drawn and place names use German names first", () => {
  const road = style.layers.find((l) => l.id === "road");
  assert.deepEqual(JSON.parse(JSON.stringify(road.filter[1][2][1])).slice(0, 3), ["path", "rail", "transit"]);
  const place = style.layers.find((l) => l.id === "place-large");
  assert.deepEqual(JSON.parse(JSON.stringify(place.layout["text-field"])), ["coalesce", ["get", "name:de"], ["get", "name"]]);
});
