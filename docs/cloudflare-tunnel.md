# Routenplaner unter der eigenen Domain (Cloudflare Tunnel)

Der Anschluss laesst von aussen nur wenige IPv4-Ports durch (12140-12159), 443 kommt nicht an. Der Tunnel baut deshalb von
innen eine Verbindung zu Cloudflare auf; die Fritzbox braucht keine Freigabe. Ergebnis: `https://fahrplan.<domain>` auf Port 443
mit Zertifikat von Cloudflare. Traefik und DuckDNS koennen als Rueckfall weiterlaufen (`https://hvv-api.duckdns.org:12140`).

## Einrichtung (einmalig, im Browser)
1. Kostenloses Konto auf cloudflare.com, "Add a domain", die Domain eintragen, Free-Tarif.
2. Cloudflare nennt zwei Nameserver; beim Registrar der Domain die Nameserver darauf umstellen (dauert Minuten bis Stunden).
   Vorhandene DNS-Eintraege (Mail usw.) vorher in Cloudflare uebernehmen/pruefen.
3. Zero Trust > Networks > Tunnels > Create a tunnel > Cloudflared. Name z. B. `pi-hvv`.
4. Den **Token** aus dem angezeigten Befehl kopieren (langer Text nach `--token`).
5. Public Hostname: Subdomain `fahrplan`, Domain waehlen, Service `HTTP` und `127.0.0.1:8090`.

## Auf dem Pi
`TUNNEL_TOKEN=<token>` in `.env` eintragen (nicht committen), dann:

    sudo -n docker compose -f /home/florian/projects/hvv-gti/docker-compose.yml --profile tunnel up -d hvv-tunnel

Pruefen: `https://fahrplan.<domain>` im Handy-Browser. Logs: `sudo -n docker logs hvv-tunnel`.

## Hinweise
- Cloudflare beendet TLS und sieht den Verkehr. Die Seite ist oeffentlich ohne Anmeldung; bei Bedarf Cloudflare Access davorsetzen.
- Der Standort-Knopf braucht HTTPS: mit der eigenen Domain ist das gegeben.
