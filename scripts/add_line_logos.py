"""Fuegt den Grafana-Tabellen mit Linienspalte eine Logo-Spalte hinzu (idempotent).

Die Logos liegen unter /public/img/hvv/<Linie>.svg (Verzeichnis data/icons wird in den Grafana-Container gemountet,
befuellt mit `python -m hvv_gti icons`).
"""

import json
import sys
from pathlib import Path

PREFIX = "/public/img/hvv/"
NEEDLE = "Linie: r.line"
REPLACEMENT = f'Logo: "{PREFIX}" + r.line + ".svg", Linie: r.line'
LOGO_OVERRIDE = {
    "matcher": {"id": "byName", "options": "Logo"},
    "properties": [
        {"id": "custom.cellOptions", "value": {"type": "image", "alt": "Linie", "title": "Linie"}},
        {"id": "custom.width", "value": 64},
        {"id": "displayName", "value": " "},
    ],
}


def patch(panel: dict) -> bool:
    if panel.get("type") != "table":
        return False
    changed = False
    for target in panel.get("targets", []):
        query = target.get("query", "")
        if NEEDLE in query and "Logo:" not in query:
            target["query"] = query.replace(NEEDLE, REPLACEMENT)
            changed = True
    if not changed:
        return False
    overrides = panel.setdefault("fieldConfig", {}).setdefault("overrides", [])
    overrides.append(LOGO_OVERRIDE)
    transformations = panel.setdefault("transformations", None) or []
    organize = next((t for t in transformations if t.get("id") == "organize"), None)
    if organize is None:
        organize = {"id": "organize", "options": {"excludeByName": {}, "indexByName": {}, "renameByName": {}}}
        transformations.append(organize)
    index = organize["options"].setdefault("indexByName", {})
    if index:
        index.update({"Logo": -1})
        ordered = sorted(index, key=lambda k: index[k])
        organize["options"]["indexByName"] = {k: i for i, k in enumerate(ordered)}
    else:
        index.update({"Logo": 0, "Linie": 1})
    panel["transformations"] = transformations
    return True


def main() -> int:
    for path in sorted(Path(__file__).resolve().parent.parent.glob("grafana/dashboards/*.json")):
        dashboard = json.loads(path.read_text("utf-8"))
        hits = [p.get("title") for p in dashboard.get("panels", []) if patch(p)]
        if hits:
            path.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n", "utf-8")
            print(f"{path.name}: {', '.join(hits)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
