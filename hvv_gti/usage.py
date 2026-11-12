from .client import ApiCall
from .influx import line_protocol

MEASUREMENT = "hvv_api_request"


def call_lines(calls: list[ApiCall]) -> list[str]:
    """Ein Punkt je GTI-Aufruf (Zeitstempel = Start). Das Dashboard zaehlt sie je Zeitfenster zu Anfragen pro Sekunde."""
    return [
        line_protocol(
            MEASUREMENT,
            {"endpoint": c.endpoint, "status": c.status},
            {"latency_ms": round(c.latency_s * 1000), "bytes": c.response_bytes},
            c.started_ns,
        )
        for c in calls
    ]
