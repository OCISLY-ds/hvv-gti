"""Doppelte Fahrten in getVehicleMap: bei verspaeteten Zuegen liefert die GTI dieselbe Fahrt zweimal.

Gemessen am 07.11.2026 (S3 am Hauptbahnhof, Tafel: +6 min): eine Fahrt mit `realtimeDelay` 6 (Abfahrt 20:12) und daneben eine
"Geisterfahrt" zur Planzeit (Abfahrt 20:06) mit `realtimeDelay` 0. Beide haben dieselben Abschnitte und dieselbe planmaessige
Startzeit (Startzeit minus Verspaetung), nur die Verspaetung unterscheidet sich. Welche der beiden zuerst in der Antwort steht,
wechselt von Abruf zu Abruf. Wer einfach die erste nimmt, zeigt den Zug mal puenktlich und mal verspaetet (Karte) bzw. schreibt
mal 0 und mal die echte Verspaetung auf denselben Messpunkt (Influx: gleiche Serie, gleicher Zeitstempel).

Die Fahrt mit der groesseren Verspaetung gilt, die Geisterfahrt hat nie mehr als die echte. Bei gleicher Verspaetung bleibt
die erste (echte Dublette).
"""


def trip_keys(line: str, segments: list[dict]) -> list[tuple]:
    """Je Abschnitt (Linie, Haltepunktpaar, planmaessige Startminute); aendert sich zwischen Abrufen nicht."""
    def stop(s: dict, side: str) -> str:  # die GTI liefert immer Haltepunkte; Station bzw. Name nur als Rueckfall
        return s.get(f"{side}StopPointKey") or s.get(f"{side}StationKey") or s.get(f"{side}StationName") or "?"

    return [(line, f"{stop(s, 'start')}>{stop(s, 'end')}", (s["startDateTime"] - 60 * (s.get("realtimeDelay") or 0)) // 60) for s in segments]


def max_delay(segments: list[dict]) -> int:
    return max((s.get("realtimeDelay") or 0 for s in segments), default=0)


def drop_ghosts(journeys: list[dict]) -> list[dict]:
    """Von Fahrten mit gemeinsamen Abschnitten bleibt die mit der groessten Verspaetung; Reihenfolge bleibt erhalten."""
    alive: dict[int, int] = {}  # Index -> Verspaetung
    owner: dict[tuple, int] = {}
    for i, journey in enumerate(journeys):
        segments = journey.get("segments", [])
        keys = trip_keys(journey.get("line", {}).get("name") or "?", segments)
        delay = max_delay(segments)
        rivals = {owner[k] for k in keys if k in owner and owner[k] in alive}
        if rivals and delay <= max(alive[r] for r in rivals):
            continue
        for r in rivals:
            del alive[r]
        alive[i] = delay
        for k in keys:
            owner[k] = i
    return [journeys[i] for i in sorted(alive)]
