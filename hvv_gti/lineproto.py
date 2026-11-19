"""Line-Protocol (InfluxDB-Textformat) lesen: Gegenstueck zu `influx.line_protocol`.

Das Format ist der Austauschweg zwischen dem Sammler (Pi, schreibt Dateien) und der Datenbank (neue Maschine, liest sie ein).
`parse_line` liefert (Messung, Tags, Felder, Zeitstempel in ns); Feldwerte sind int, float, bool oder str.
"""


class LineError(ValueError):
    pass


def _unescape(text: str) -> str:
    if "\\" not in text:
        return text
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in '\\,= "':
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _split(text: str, sep: str) -> list[str]:
    """Teilt an unmaskierten `sep` (Zeichen nach einem Rueckwaertsschraegstrich gehoeren zum Text)."""
    parts, cur, i = [], [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            cur.append(text[i : i + 2])
            i += 2
            continue
        if c == sep:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


def _sections(line: str) -> tuple[str, str, str]:
    """(Messung+Tags, Felder, Zeitstempel): Leerzeichen innerhalb maskierter Zeichen oder Feld-Strings zaehlen nicht."""
    cut, in_quote, i, n = [], False, 0, len(line)
    in_fields = False
    while i < n:
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c == '"' and in_fields:
            in_quote = not in_quote
        elif c == " " and not in_quote:
            cut.append(i)
            in_fields = True
            if len(cut) == 2:
                break
        i += 1
    if not cut:
        raise LineError("keine Felder")
    head = line[: cut[0]]
    fields = line[cut[0] + 1 : cut[1]] if len(cut) > 1 else line[cut[0] + 1 :]
    stamp = line[cut[1] + 1 :].strip() if len(cut) > 1 else ""
    return head, fields, stamp


def _field_value(raw: str):
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return _unescape(raw[1:-1])
    if raw in ("t", "T", "true", "True", "TRUE"):
        return True
    if raw in ("f", "F", "false", "False", "FALSE"):
        return False
    if raw.endswith("i"):
        return int(raw[:-1])
    if raw.endswith("u"):
        return int(raw[:-1])
    return float(raw)


def _fields(text: str) -> dict:
    out, cur, in_quote, i = {}, [], False, 0
    items = []
    while i < len(text):
        c = text[i]
        if c == "\\":
            cur.append(text[i : i + 2])
            i += 2
            continue
        if c == '"':
            in_quote = not in_quote
        if c == "," and not in_quote:
            items.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    items.append("".join(cur))
    for item in items:
        parts = _split(item, "=")
        if len(parts) < 2:
            raise LineError(f"Feld ohne Wert: {item!r}")
        out[_unescape(parts[0])] = _field_value("=".join(parts[1:]))
    return out


def parse_line(line: str) -> tuple[str, dict[str, str], dict, int | None]:
    line = line.rstrip("\r\n")
    head, fields, stamp = _sections(line)
    pieces = _split(head, ",")
    tags = {}
    for piece in pieces[1:]:
        parts = _split(piece, "=")  # erstes unmaskiertes "=" trennt Schluessel und Wert
        if len(parts) < 2:
            raise LineError(f"Tag ohne Wert: {piece!r}")
        tags[_unescape(parts[0])] = _unescape("=".join(parts[1:]))
    try:
        return _unescape(pieces[0]), tags, _fields(fields), int(stamp) if stamp else None
    except ValueError as exc:
        raise LineError(str(exc)) from exc
