import urllib.error
import urllib.parse
import urllib.request


class InfluxError(Exception):
    pass


def _esc(value: str, chars: str) -> str:
    value = value.replace("\\", "\\\\")
    for ch in chars:
        value = value.replace(ch, "\\" + ch)
    return value


def _field_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return repr(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def line_protocol(measurement: str, tags: dict[str, str], fields: dict, timestamp_ns: int) -> str:
    tag_part = "".join(
        f",{_esc(k, ',= ')}={_esc(str(v), ',= ')}" for k, v in sorted(tags.items()) if v not in (None, "")
    )
    field_part = ",".join(f"{_esc(k, ',= ')}={_field_value(v)}" for k, v in fields.items())
    return f"{_esc(measurement, ', ')}{tag_part} {field_part} {timestamp_ns}"


class InfluxWriter:
    def __init__(self, url: str, org: str, bucket: str, token: str, timeout: float = 15.0):
        query = urllib.parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
        self._url = f"{url.rstrip('/')}/api/v2/write?{query}"
        self._token = token
        self._timeout = timeout

    def write(self, lines: list[str]) -> None:
        if not lines:
            return
        req = urllib.request.Request(
            self._url,
            data="\n".join(lines).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Token {self._token}", "Content-Type": "text/plain; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout):
                pass
        except urllib.error.HTTPError as exc:
            raise InfluxError(f"Influx HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise InfluxError(f"Influx nicht erreichbar: {exc}") from exc
