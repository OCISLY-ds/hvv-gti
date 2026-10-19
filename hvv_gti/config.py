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


@dataclass(frozen=True)
class Route:
    """Messpunkt einer Strecke: Fahrten der Linie `line` ab `station` (nur Richtungen aus `directions`).

    `name` ist der Wert des Tags `route` in InfluxDB. Mehrere Eintraege duerfen denselben Namen tragen
    (z.B. Neugraben und Hauptbahnhof fuer "S5 Neugraben -> Elbgaustrasse"). Leere `directions` fangen alle
    Richtungen ab und muessen daher nach den spezifischeren Eintraegen derselben Linie und Haltestelle stehen.
    """

    name: str
    line: str
    service_type: str
    station: str
    station_id: str
    directions: tuple[str, ...] = ()
    max_list: int = 40


STADE = ("Stade", "Master:8000089")
NEUGRABEN = ("Neugraben", "Master:41951")
HAUPTBAHNHOF = ("Hauptbahnhof", "Master:9910950")
BERGEDORF = ("Bergedorf", "Master:25950")
HARBURG_RATHAUS = ("Harburg Rathaus", "Master:40950")

# Stand 19.10.2026: Die S5 faehrt geteilt (Stade-Neugraben, Neugraben-Elbgaustrasse ueber Hbf), die S7 nur
# Bergedorf-Aumuehle (ab Bergedorf Richtung Hamburg faehrt der Ersatzbus S7-SEV nach Berliner Tor).
# Die Richtungen sind Teilstrings des GTI-Felds `direction` (Gross-/Kleinschreibung egal).
# Der GTI-Filter nach Linie wird vom Server ignoriert, deshalb wird clientseitig nach `line` gefiltert.
ROUTES: tuple[Route, ...] = (
    Route("S5 Stade → Neugraben", "S5", "SBAHN", *STADE),
    Route("S5 Neugraben → Stade", "S5", "SBAHN", *NEUGRABEN, directions=("Stade", "Buxtehude")),
    Route("S5 Neugraben → Elbgaustraße", "S5", "SBAHN", *NEUGRABEN, directions=("Elbgaustraße",)),
    Route("S5 Neugraben → Elbgaustraße", "S5", "SBAHN", *HAUPTBAHNHOF, directions=("Elbgaustraße",), max_list=60),
    Route("S5 Elbgaustraße → Neugraben", "S5", "SBAHN", *HAUPTBAHNHOF, directions=("Neugraben",), max_list=60),
    Route("S7 Bergedorf → Aumühle", "S7", "SBAHN", *BERGEDORF, directions=("Aumühle",)),
    Route("S7 Bergedorf → Hamburg", "S7", "SBAHN", *BERGEDORF),
    # Im Bergedorfer Busverkehr sind in 30 Minuten rund 60 Abfahrten, die Liste muss deshalb lang sein.
    Route("S7 Bergedorf → Hamburg", "S7-SEV", "BUS", *BERGEDORF, max_list=120),
    Route("X30 Bergedorf → Harburg", "X30", "XPRESSBUS", *BERGEDORF),
    Route("X30 Harburg → Bergedorf", "X30", "XPRESSBUS", *HARBURG_RATHAUS),
)


@dataclass(frozen=True)
class Config:
    gti_user: str = ""
    gti_password: str = field(default="", repr=False)
    gti_base_url: str = "https://gti.geofox.de"
    api_version: int = 63
    min_request_interval: float = 1.2
    routes: tuple[Route, ...] = ROUTES
    poll_interval: float = 60.0
    max_time_offset: int = 30
    # Der GTI-Account darf keine Ankunftstafeln abfragen (HTTP 403 ArrivalList not allowed for user).
    collect_arrivals: bool = False
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
            poll_interval=float(e.get("HVV_POLL_INTERVAL", d.poll_interval)),
            max_time_offset=int(e.get("HVV_MAX_TIME_OFFSET", d.max_time_offset)),
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
