import base64
import gzip
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")


class GtiError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class GtiAuthError(GtiError):
    pass


class GtiPermissionError(GtiError):
    """Zugang gueltig, aber die Methode ist fuer den Account nicht freigeschaltet."""


def sign(password: str, body: bytes) -> str:
    digest = hmac.new(password.encode("utf-8"), body, hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def gti_time(moment: datetime) -> dict[str, str]:
    local = moment.astimezone(TZ)
    return {"date": local.strftime("%d.%m.%Y"), "time": local.strftime("%H:%M")}


class GtiClient:
    def __init__(
        self,
        user: str,
        password: str,
        base_url: str = "https://gti.geofox.de",
        version: int = 63,
        min_interval: float = 1.2,
        timeout: float = 20.0,
    ):
        self.user = user
        self._password = password
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request = 0.0
        self.last_response_bytes = 0
        self.last_latency = 0.0

    def _throttle(self) -> None:
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def post(self, path: str, payload: dict) -> dict:
        body = json.dumps(
            {"language": "de", "version": self.version, **payload},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/gti/public/{path}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "geofox-auth-type": "HmacSHA1",
                "geofox-auth-user": self.user,
                "geofox-auth-signature": sign(self._password, body),
                "X-TraceId": str(uuid.uuid4()),
                "User-Agent": "hvv-gti/0.1",
            },
        )
        self._throttle()
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 403 and "not allowed for user" in detail:
                cls = GtiPermissionError
            else:
                cls = GtiAuthError if exc.code in (401, 403) else GtiError
            raise cls(f"HTTP {exc.code} bei {path}: {detail}", code=str(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GtiError(f"Verbindungsfehler bei {path}: {exc}") from exc
        self.last_latency = time.monotonic() - started
        self.last_response_bytes = len(raw)
        data = json.loads(raw)
        if data.get("returnCode") != "OK":
            raise GtiError(
                f"{path}: {data.get('returnCode')} {data.get('errorText', '')} {data.get('errorDevInfo', '')}".strip(),
                code=data.get("returnCode"),
            )
        return data

    def init(self) -> dict:
        return self.post("init", {})

    def check_name(self, name: str, max_list: int = 10) -> list[dict]:
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
        payload = {
            "station": station,
            "time": gti_time(when),
            "maxList": max_list,
            "maxTimeOffset": max_time_offset,
            "useRealtime": True,
            "departure": departure,
        }
        if service_types:
            payload["serviceTypes"] = service_types
        return self.post("departureList", payload)

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
