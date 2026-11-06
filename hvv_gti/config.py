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
    """Messpunkt einer Linie: Fahrten der GTI-Linie `line` ab `station` (nur Richtungen aus `directions`).

    `name` ist die Linie, wie sie im Dashboard gewaehlt wird (Tag `route`, z.B. "S7" fuer Zug und Ersatzbus S7-SEV).
    `heading` ist die vereinheitlichte Richtung (Tag `direction`, z.B. "Hamburg" fuer die Ziele Hamburg Hbf und
    Hamburg-Harburg); das GTI-Ziel steht unveraendert im Tag `destination`. Leere `directions` fangen alle
    Richtungen ab und muessen daher nach den spezifischeren Eintraegen derselben Linie und Haltestelle stehen.
    """

    name: str
    line: str
    service_type: str
    station: str
    station_id: str
    heading: str
    directions: tuple[str, ...] = ()
    max_list: int = 40


STADE = ("Stade", "Master:8000089")
NEUGRABEN = ("Neugraben", "Master:41951")
HAUPTBAHNHOF = ("Hauptbahnhof", "Master:9910950")
BERGEDORF = ("Bergedorf", "Master:25950")
HARBURG_RATHAUS = ("Harburg Rathaus", "Master:40950")
RATHAUSMARKT = ("Rathausmarkt", "Master:11003")

# Stand 19.10.2026: Die S5 faehrt geteilt (Stade-Neugraben, Neugraben-Elbgaustrasse ueber Hbf), die S7 nur
# Bergedorf-Aumuehle (ab Bergedorf Richtung Hamburg faehrt der Ersatzbus S7-SEV nach Berliner Tor).
# Die Richtungen sind Teilstrings des GTI-Felds `direction` (Gross-/Kleinschreibung egal).
# Der GTI-Filter nach Linie wird vom Server ignoriert, deshalb wird clientseitig nach `line` gefiltert.
ROUTES: tuple[Route, ...] = (
    Route("S5", "S5", "SBAHN", *STADE, heading="Neugraben"),
    Route("S5", "S5", "SBAHN", *NEUGRABEN, heading="Stade", directions=("Stade", "Buxtehude")),
    Route("S5", "S5", "SBAHN", *NEUGRABEN, heading="Elbgaustraße", directions=("Elbgaustraße",)),
    Route("S5", "S5", "SBAHN", *HAUPTBAHNHOF, heading="Elbgaustraße", directions=("Elbgaustraße",), max_list=60),
    Route("S5", "S5", "SBAHN", *HAUPTBAHNHOF, heading="Neugraben", directions=("Neugraben",), max_list=60),
    Route("S7", "S7", "SBAHN", *BERGEDORF, heading="Aumühle", directions=("Aumühle",)),
    Route("S7", "S7", "SBAHN", *BERGEDORF, heading="Hamburg"),
    # Im Bergedorfer Busverkehr sind in 30 Minuten rund 60 Abfahrten, die Liste muss deshalb lang sein.
    Route("S7", "S7-SEV", "BUS", *BERGEDORF, heading="Hamburg", max_list=120),
    Route("X30", "X30", "XPRESSBUS", *BERGEDORF, heading="Harburg"),
    Route("X30", "X30", "XPRESSBUS", *HARBURG_RATHAUS, heading="Bergedorf"),
    # Bus 234 ab Bergedorf (Bf.) bis Lohbruegger Weg, von dort 3 Minuten zu Fuss zur Leuschnerstrasse (nur Adresse, keine Haltestelle).
    # Gleiche Abfrage wie S7-SEV (Bergedorf, BUS), kostet keinen zusaetzlichen Aufruf.
    Route("234", "234", "BUS", *BERGEDORF, heading="Leuschnerstraße", max_list=120),
    # Bus 4 (Hochbahn): Eidelstedt - Eimsbuettel - Dammtor - Jungfernstieg - Ueberseequartier. Messpunkt ist der Rathausmarkt
    # in der Mitte der Linie (beide Richtungen). Wildacker und Langenfelder Damm (Kurzfahrt) liegen beide im Westen.
    Route("4", "4", "BUS", *RATHAUSMARKT, heading="Überseequartier", directions=("Osakaallee",), max_list=120),
    Route("4", "4", "BUS", *RATHAUSMARKT, heading="Eidelstedt", max_list=120),
    # RE5 Cuxhaven-Stade-Hamburg: Abfahrten in Stade in beide Richtungen (Hamburg: Ziel Hbf oder Harburg).
    Route("RE5", "RE5", "ZUG", *STADE, heading="Hamburg", directions=("Hamburg",)),
    Route("RE5", "RE5", "ZUG", *STADE, heading="Cuxhaven", directions=("Cuxhaven",)),
)


# Zuglauf Stade-Hamburg: getVehicleMap liefert je Fahrt alle Abschnitte mit Verspaetung. Die Haltestellen stehen in
# Fahrtrichtung Hamburg, ihr Index ist die x-Achse im Dashboard. Namen wie die GTI sie liefert; `aliases` sind
# abweichende Schreibweisen derselben Haltestelle (der RE5 nennt Harburg "Hamburg-Harburg").
@dataclass(frozen=True)
class CorridorStation:
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Corridor:
    name: str
    stations: tuple[CorridorStation, ...]
    lines: tuple[tuple[str, str], ...]  # (Linie, vehicleType der getVehicleMap)
    bbox: tuple[float, float, float, float]  # lon1, lat1, lon2, lat2 (WGS84)
    lookback_s: int = 90 * 60
    lookahead_s: int = 90 * 60


CORRIDOR = Corridor(
    name="Stade – Hamburg",
    stations=(
        CorridorStation("Stade"),
        CorridorStation("Dollern"),
        CorridorStation("Horneburg"),
        CorridorStation("Neukloster"),
        CorridorStation("Buxtehude"),
        CorridorStation("Neu Wulmstorf"),
        CorridorStation("Fischbek"),
        CorridorStation("Neugraben"),
        CorridorStation("Neuwiedenthal"),
        CorridorStation("Heimfeld (TU Hamburg)"),
        CorridorStation("Harburg Rathaus"),
        CorridorStation("Harburg", ("Hamburg-Harburg",)),
        CorridorStation("Wilhelmsburg"),
        CorridorStation("Veddel (BallinStadt)"),
        CorridorStation("Elbbrücken"),
        CorridorStation("Hammerbrook (City Süd)"),
        CorridorStation("Hamburg Hbf"),
    ),
    lines=(("RE5", "R_BAHN"), ("S5", "S_BAHN")),
    bbox=(9.40, 53.40, 10.05, 53.65),
)


# Gesamtes S- und U-Bahn-Netz: ein getVehicleMap-Aufruf je Zyklus fuer alle Linien beider Verkehrsmittel. Geschrieben
# werden nur Halte der naechsten `horizon_s` Sekunden, damit jeder Halt hoechstens einige Male (und zuletzt kurz vor
# der Durchfahrt) ueberschrieben wird, statt das ganze Fahrtenfenster bei jedem Zyklus neu zu speichern.
@dataclass(frozen=True)
class Network:
    name: str
    vehicle_types: tuple[str, ...]
    bbox: tuple[float, float, float, float]  # lon1, lat1, lon2, lat2 (WGS84)
    horizon_s: int = 300
    measurement: str = "hvv_network_stop"
    line_prefixes: tuple[str, ...] = ()  # nur Linien, deren Name so beginnt (leer: alle)


NETWORK = Network(
    name="S- und U-Bahn Hamburg",
    vehicle_types=("S_BAHN", "U_BAHN"),
    bbox=(9.40, 53.30, 10.40, 53.80),
)


# Alle X-Busse (Xpressbus, getVehicleMap-Typ XPRESSBUS): gleiches Format wie das S-/U-Bahn-Netz, aber eigene Messung, damit
# die S-/U-Bahn-Dashboards unveraendert bleiben. Die X-Linien fahren weit ins Umland, deshalb das groessere Gebiet
# (entspricht dem Kartenausschnitt in map/tiles/build.sh).
XBUS = Network(
    name="X-Busse HVV",
    vehicle_types=("XPRESSBUS",),
    bbox=(8.60, 53.10, 10.70, 53.95),
    measurement="hvv_xbus_stop",
)


# Alle Regionalexpress- und Regionalbahn-Linien (RE und RB, getVehicleMap-Typ R_BAHN): eigener Aufruf, eigene Messung, Gebiet von
# Cuxhaven bis Luebeck und von Stade bis Schleswig-Holstein-Mitte. Die Fahrten sind lang, daher der groessere Horizont.
REGIONAL = Network(
    name="Regionalzuege HVV (RE, RB)",
    vehicle_types=("R_BAHN",),
    bbox=(8.45, 53.10, 10.95, 54.05),
    horizon_s=900,
    measurement="hvv_regional_stop",
    line_prefixes=("RE", "RB"),
)


@dataclass(frozen=True)
class Config:
    gti_user: str = ""
    gti_password: str = field(default="", repr=False)
    gti_base_url: str = "https://gti.geofox.de"
    api_version: int = 63
    min_request_interval: float = 1.2
    routes: tuple[Route, ...] = ROUTES
    corridor: Corridor | None = CORRIDOR
    network: Network | None = NETWORK
    xbus: Network | None = XBUS
    regional: Network | None = REGIONAL
    poll_interval: float = 60.0
    # Stoerungsmeldungen aendern sich selten und die Antwort ist gross (~75 KB); 0 schaltet sie ab.
    announcement_interval: float = 300.0
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
            announcement_interval=float(e.get("HVV_ANNOUNCEMENT_INTERVAL", d.announcement_interval)),
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
