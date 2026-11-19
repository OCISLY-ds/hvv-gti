"""Minimaler GitHub-Releases-Client (nur Standardbibliothek): Datenpakete als Release-Anhaenge eines privaten Repos hochladen und holen.

Der Pi und die neue Maschine sprechen nie direkt miteinander; GitHub ist der gemeinsame Briefkasten. Ein Release je Monat (`data-YYYY-MM`)
haelt die Pakete des Monats, `data-history` die einmalige Historie. Die Pakete enthalten nur Messwerte, keine Zugangsdaten.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("hvv_gti.github")

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
AGENT = "hvv-gti-data/0.1"


class GithubError(OSError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # Weiterleitung selbst folgen, damit der Token nicht zum Speicher-Host mitgeschickt wird
        return None


class Releases:
    def __init__(self, repo: str, token: str, api: str = API, uploads: str = UPLOADS, timeout: float = 120.0):
        if "/" not in repo or not token:
            raise GithubError("GITHUB_REPO (owner/name) und GITHUB_TOKEN fehlen")
        self.repo, self.token, self.api, self.uploads, self.timeout = repo, token, api.rstrip("/"), uploads.rstrip("/"), timeout

    def _headers(self, extra: dict | None = None) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "User-Agent": AGENT, "X-GitHub-Api-Version": "2022-11-28", **(extra or {})}

    def _json(self, method: str, url: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers({"Content-Type": "application/json"} if data else {}))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise GithubError(f"GitHub {method} {url}: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GithubError(f"GitHub nicht erreichbar: {exc}") from exc
        return json.loads(raw) if raw else None

    def list_releases(self, prefix: str = "") -> list[dict]:
        found, page = [], 1
        while True:
            batch = self._json("GET", f"{self.api}/repos/{self.repo}/releases?per_page=100&page={page}")
            found += [r for r in batch if r["tag_name"].startswith(prefix)]
            if len(batch) < 100:
                return found
            page += 1

    def release(self, tag: str, create: bool = True) -> dict | None:
        for r in self.list_releases(tag):
            if r["tag_name"] == tag:
                return r
        if not create:
            return None
        log.info("Release %s wird angelegt", tag)
        return self._json("POST", f"{self.api}/repos/{self.repo}/releases", {"tag_name": tag, "name": tag, "body": "Messdaten des HVV-Sammlers (Line-Protocol, gzip). Keine Zugangsdaten.", "draft": False, "prerelease": False})

    def assets(self, release: dict) -> list[dict]:
        out, page = [], 1
        while True:
            batch = self._json("GET", f"{self.api}/repos/{self.repo}/releases/{release['id']}/assets?per_page=100&page={page}")
            out += batch
            if len(batch) < 100:
                return out
            page += 1

    def upload(self, release: dict, path: Path, name: str | None = None) -> dict | None:
        """Datei als Anhang hochladen; gibt es den Namen schon, passiert nichts (None)."""
        name = name or path.name
        if any(a["name"] == name for a in self.assets(release)):
            return None
        size = path.stat().st_size
        url = f"{self.uploads}/repos/{self.repo}/releases/{release['id']}/assets?" + urllib.parse.urlencode({"name": name})
        with path.open("rb") as fh:
            req = urllib.request.Request(url, data=fh, method="POST", headers=self._headers({"Content-Type": "application/gzip", "Content-Length": str(size)}))
            try:
                with urllib.request.urlopen(req, timeout=max(self.timeout, size / 50_000)) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                raise GithubError(f"Upload {name}: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise GithubError(f"Upload {name}: {exc}") from exc

    def download(self, asset: dict, dest: Path) -> None:
        url = f"{self.api}/repos/{self.repo}/releases/assets/{asset['id']}"
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(url, headers=self._headers({"Accept": "application/octet-stream"}))
        try:
            try:
                resp = opener.open(req, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in (301, 302, 303, 307, 308):
                    raise GithubError(f"Download {asset['name']}: HTTP {exc.code}") from exc
                resp = urllib.request.urlopen(urllib.request.Request(exc.headers["Location"], headers={"User-Agent": AGENT}), timeout=self.timeout)
            with resp, dest.open("wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GithubError(f"Download {asset['name']}: {exc}") from exc
