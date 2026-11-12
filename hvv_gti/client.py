"""Einfacher Client fuer die HVV-Schnittstelle (GTI, Geofox).

Ablauf jedes Aufrufs: JSON-Text bauen -> mit dem Passwort signieren -> per HTTP-POST schicken -> Antwort als JSON lesen.
"""

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")  # Die GTI erwartet Datum und Uhrzeit in deutscher Ortszeit.
MAX_CALL_LOG = 5000  # Soviele Protokolleintraege bleiben hoechstens im Speicher (z. B. wenn Influx nicht erreichbar ist).


# ---- Fehler: drei Arten, damit der Rest des Programms unterschiedlich reagieren kann ----


class GtiError(Exception):
    """Allgemeiner Fehler (Netz, Serverfehler, returnCode != OK). `code` ist der HTTP-Status oder returnCode."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class GtiAuthError(GtiError):
    """Benutzer oder Passwort falsch (HTTP 401/403)."""


class GtiPermissionError(GtiError):
    """Zugang gueltig, aber die Methode ist fuer den Account nicht freigeschaltet."""


@dataclass(frozen=True)
class ApiCall:
    """Ein Eintrag im Aufrufprotokoll (fuer die Auslastungsstatistik). `status` ist "ok", HTTP-Status, returnCode oder "network"."""

    endpoint: str
    started_ns: int
    latency_s: float
    response_bytes: int
    status: str


# ---- Hilfsfunktionen ----


def sign(password: str, body: bytes) -> str:
    """Signatur: HMAC-SHA1 ueber den Anfragetext mit dem Passwort als Schluessel, base64-kodiert. Das Passwort selbst wird nie gesendet."""
    digest = hmac.new(password.encode("utf-8"), body, hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def gti_time(moment: datetime) -> dict[str, str]:
    """Zeitpunkt im GTI-Format: {"date": "15.01.2026", "time": "09:05"}."""
    local = moment.astimezone(TZ)
    return {"date": local.strftime("%d.%m.%Y"), "time": local.strftime("%H:%M")}


# ---- Der Client ----


class GtiClient:
    def __init__(
        self,
        user: str,
        password: str,
        base_url: str = "https://gti.geofox.de",
        version: int = 63,
        min_interval: float = 1.2,  # Mindestabstand zwischen zwei Aufrufen in Sekunden (die GTI sperrt bei mehr als ~1 Aufruf/s)
        timeout: float = 20.0,
    ):
        self.user = user
        self._password = password
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request = 0.0
        self.call_log: list[ApiCall] = []
        self._lock = threading.Lock()  # die Karte ruft aus mehreren Threads auf; es laeuft immer nur ein Aufruf

    def take_calls(self) -> list[ApiCall]:
        """Gibt das Aufrufprotokoll heraus und leert es."""
        calls, self.call_log = self.call_log, []
        return calls

    def post(self, path: str, payload: dict) -> dict:
        """Ein GTI-Aufruf: `path` ist der Methodenname (z. B. "departureList"), `payload` die Parameter. Gibt die Antwort als dict zurueck."""
        with self._lock:
            # 1. Pause, falls der letzte Aufruf weniger als `min_interval` Sekunden her ist.
            pause = self._last_request + self.min_interval - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            self._last_request = time.monotonic()

            # 2. Anfragetext bauen und signieren.
            body = json.dumps({"language": "de", "version": self.version, **payload}, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                f"{self.base_url}/gti/public/{path}",
                data=body,  # data gesetzt = HTTP-POST
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "geofox-auth-type": "HmacSHA1",
                    "geofox-auth-user": self.user,
                    "geofox-auth-signature": sign(self._password, body),
                },
            )

            # 3. Abschicken und Antwort lesen. Jeder Fehler wird zu einem GtiError und im Protokoll vermerkt.
            started_ns, started = time.time_ns(), time.monotonic()
            size, status = 0, "network"
            try:
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        raw = response.read()
                except urllib.error.HTTPError as exc:  # Server hat mit Fehlerstatus geantwortet (401, 403, 500, ...)
                    detail = exc.read().decode("utf-8", "replace")[:300]
                    if exc.code == 403 and "not allowed for user" in detail:
                        error = GtiPermissionError
                    elif exc.code in (401, 403):
                        error = GtiAuthError
                    else:
                        error = GtiError
                    status = str(exc.code)
                    raise error(f"HTTP {exc.code} bei {path}: {detail}", code=status) from exc
                except (urllib.error.URLError, TimeoutError) as exc:  # Server nicht erreichbar
                    raise GtiError(f"Verbindungsfehler bei {path}: {exc}") from exc

                size = len(raw)
                data = json.loads(raw)
                if data.get("returnCode") != "OK":  # HTTP war in Ordnung, aber die GTI meldet einen Fachfehler
                    status = str(data.get("returnCode"))
                    raise GtiError(f"{path}: {status} {data.get('errorText', '')}".strip(), code=status)
                status = "ok"
                return data
            finally:
                self.call_log.append(ApiCall(path, started_ns, time.monotonic() - started, size, status))
                del self.call_log[:-MAX_CALL_LOG]

    # ---- Die einzelnen GTI-Methoden: jede baut nur die Parameter und ruft `post` ----

    def init(self) -> dict:
        """Verbindungstest: liefert z. B. den Fahrplanzeitraum."""
        return self.post("init", {})

    def check_name(self, name: str, max_list: int = 10) -> list[dict]:
        """Haltestellen zu einem Namen suchen (liefert Name und ID)."""
        data = self.post("checkName", {"theName": {"name": name, "type": "STATION"}, "maxList": max_list})
        return data.get("results", [])

    def departure_list(
        self,
        station: dict,
        when: datetime,
        *,
        max_list: int = 40,
        max_time_offset: int = 30,
        departure: bool = True,
        service_types: list[str] | None = None,
    ) -> dict:
        """Abfahrtstafel einer Haltestelle ab dem Zeitpunkt `when` (inkl. Echtzeit-Verspaetung)."""
        payload = {
            "station": station,
            "time": gti_time(when),
            "maxList": max_list,
            "maxTimeOffset": max_time_offset,  # nur Abfahrten der naechsten N Minuten
            "useRealtime": True,
            "departure": departure,
        }
        if service_types:
            payload["serviceTypes"] = service_types
        return self.post("departureList", payload)

    def get_announcements(self, names: list[str]) -> dict:
        """Stoerungs- und Baustellenmeldungen zu den genannten Linien."""
        return self.post("getAnnouncements", {"names": names, "full": True})

    def get_vehicle_map(
        self,
        lower_left: tuple[float, float],
        upper_right: tuple[float, float],
        period_begin: int,
        period_end: int,
        *,
        realtime: bool = True,
        without_coords: bool = True,
        vehicle_types: list[str] | None = None,
    ) -> dict:
        """Alle Fahrten in einem Rechteck (lon, lat) und Zeitraum (Unix-Sekunden); `vehicle_types` z. B. ["S_BAHN"]."""
        payload = {
            "boundingBox": {
                "lowerLeft": {"x": lower_left[0], "y": lower_left[1], "type": "EPSG_4326"},
                "upperRight": {"x": upper_right[0], "y": upper_right[1], "type": "EPSG_4326"},
            },
            "periodBegin": period_begin,
            "periodEnd": period_end,
            "withoutCoords": without_coords,
            "coordinateType": "EPSG_4326",
            "realtime": realtime,
        }
        if vehicle_types:
            payload["vehicleTypes"] = vehicle_types
        return self.post("getVehicleMap", payload)
