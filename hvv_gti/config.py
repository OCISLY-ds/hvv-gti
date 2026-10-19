import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def parse_stations(raw: str) -> list[tuple[str, str | None]]:
    stations = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        name, sep, station_id = part.partition("=")
        stations.append((name.strip(), station_id.strip() if sep and station_id.strip() else None))
    return stations


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    gti_user: str = ""
    gti_password: str = field(default="", repr=False)
    gti_base_url: str = "https://gti.geofox.de"
    api_version: int = 63
    min_request_interval: float = 1.2
    line: str = "S5"
    stations: tuple[tuple[str, str | None], ...] = (("Stade", None), ("Neugraben", None), ("Hauptbahnhof", None))
    service_types: tuple[str, ...] = ("SBAHN",)
    poll_interval: float = 60.0
    max_list: int = 40
    max_time_offset: int = 30
    collect_arrivals: bool = True
    influx_url: str = "http://localhost:8086"
    influx_org: str = ""
    influx_bucket: str = "hvv"
    influx_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        e = os.environ if env is None else env
        d = cls()
        return cls(
            gti_user=e.get("HVV_GTI_USER", d.gti_user),
            gti_password=e.get("HVV_GTI_PASSWORD", d.gti_password),
            gti_base_url=e.get("HVV_GTI_BASE_URL", d.gti_base_url).rstrip("/"),
            api_version=int(e.get("HVV_API_VERSION", d.api_version)),
            min_request_interval=float(e.get("HVV_MIN_REQUEST_INTERVAL", d.min_request_interval)),
            line=e.get("HVV_LINE", d.line),
            stations=tuple(parse_stations(e["HVV_STATIONS"])) if e.get("HVV_STATIONS") else d.stations,
            service_types=tuple(s.strip() for s in e.get("HVV_SERVICE_TYPES", "SBAHN").split(",") if s.strip()),
            poll_interval=float(e.get("HVV_POLL_INTERVAL", d.poll_interval)),
            max_list=int(e.get("HVV_MAX_LIST", d.max_list)),
            max_time_offset=int(e.get("HVV_MAX_TIME_OFFSET", d.max_time_offset)),
            collect_arrivals=_bool(e.get("HVV_COLLECT_ARRIVALS", str(d.collect_arrivals))),
            influx_url=e.get("INFLUX_URL", d.influx_url).rstrip("/"),
            influx_org=e.get("INFLUX_ORG", d.influx_org),
            influx_bucket=e.get("INFLUX_BUCKET", d.influx_bucket),
            influx_token=e.get("INFLUX_TOKEN", d.influx_token),
        )

    def require_gti(self) -> None:
        if not self.gti_user or not self.gti_password:
            raise ConfigError("HVV_GTI_USER und HVV_GTI_PASSWORD fehlen (siehe .env.example)")

    def require_influx(self) -> None:
        missing = [n for n, v in (("INFLUX_ORG", self.influx_org), ("INFLUX_TOKEN", self.influx_token)) if not v]
        if missing:
            raise ConfigError(f"{', '.join(missing)} fehlt (siehe .env.example)")
