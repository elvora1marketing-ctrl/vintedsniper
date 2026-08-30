# Vinted Sniper

Discord-Bot, der Vinted-Suchen im Sekundentakt überwacht und neue Artikel sofort
als Alert mit Direktlink in einen Channel postet — für vinted.de und jede andere
Länderdomain (`.fr`, `.pl`, `.co.uk`, `.it`, …).

Du legst die Suche auf der Vinted-Website an, kopierst die URL aus der
Adresszeile und gibst sie dem Sniper. Alle Filter — Marke, Größe, Preis,
Kategorie, Zustand — kommen automatisch mit.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue) ![Betrieb](https://img.shields.io/badge/Betrieb-Docker-informational)

## Zwei Wege, dasselbe Ergebnis

|  | **Webhook-Modus** | **Bot-Modus** |
| --- | --- | --- |
| Aufwand | Webhook-URL kopieren, fertig | Bot anlegen, Token, Einladung, Rechte |
| Suchen anlegen | Zeile in `searches.toml` | `/watch add` direkt in Discord |
| Ändern | Datei bearbeiten + Neustart | Slash-Command, sofort wirksam |
| Alerts | Embed mit Links | Embed mit Buttons |
| Braucht | `ALERT_WEBHOOK_URL` | `DISCORD_TOKEN` |

Du kannst mit dem Webhook anfangen und das Bot-Token später ergänzen — beides
zusammen geht auch, dann laufen Slash-Commands und Datei-Suchen nebeneinander.
Die Datenbank bleibt dieselbe, es geht nichts verloren.

---

## Schnellstart A — Webhook (schnellster Weg)

Kein Bot, kein Token, keine Rechtevergabe.

**1. Webhook in Discord anlegen:** Channel-Einstellungen (Zahnrad neben dem
Channel) → *Integrationen* → *Webhooks* → *Neuer Webhook* → **Webhook-URL
kopieren**.

**2. Projekt einrichten:**

```bash
git clone https://github.com/elvora1marketing-ctrl/vintedsniper.git
cd vintedsniper
cp .env.example .env
cp searches.example.toml searches.toml
```

In die `.env` nur diese eine Zeile:

```
ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/…
```

**3. Suchen eintragen** in `searches.toml`:

```toml
[[search]]
name = "Nike Air Max"
url = "https://www.vinted.de/catalog?search_text=nike+air+max&price_to=60"

[[search]]
name = "Carhartt FR"
url = "https://www.vinted.fr/catalog?search_text=carhartt&price_to=80"
interval = 120
```

**4. Starten:**

```bash
docker compose up -d --build
docker compose logs -f
```

Im Channel erscheint sofort eine Startmeldung mit allen aktiven Suchen — daran
siehst du, dass der Webhook stimmt, ohne auf den ersten Treffer zu warten.

> Lege `searches.toml` **vor** dem ersten Start an. Fehlt die Datei, legt Docker
> an ihrer Stelle ein Verzeichnis an. Falls doch passiert:
> `docker compose down && rm -rf searches.toml && cp searches.example.toml searches.toml`

---

## Schnellstart B — Bot mit Slash-Commands

Mehr Einrichtung, dafür legst du Suchen direkt in Discord an:

```
/watch add url:https://www.vinted.de/catalog?search_text=nike+air+max&price_to=60
```

### 1. Discord-Bot anlegen

1. [Discord Developer Portal](https://discord.com/developers/applications) →
   **New Application**.
2. Reiter **Bot** → **Reset Token** → Token kopieren (das kommt gleich in die `.env`).
3. Reiter **OAuth2 → URL Generator**:
   - Scopes: `bot` und `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `View Channel`
4. Die erzeugte URL öffnen und den Bot auf deinen Server einladen.

Privilegierte Intents brauchst du **nicht** — der Bot liest keine Nachrichten.

### 2. Konfigurieren

```bash
git clone https://github.com/elvora1marketing-ctrl/vintedsniper.git
cd vintedsniper
cp .env.example .env
cp searches.example.toml searches.toml   # auch im Bot-Modus nötig (Docker-Mount)
$EDITOR .env          # DISCORD_TOKEN und DISCORD_GUILD_ID eintragen
```

`DISCORD_GUILD_ID` kannst du leer lassen — der Bot findet selbst heraus, auf
welchen Servern er ist, und registriert die Befehle dort. Setze die ID nur,
wenn er auf mehreren Servern läuft und die Befehle bloß auf einem erscheinen
sollen (Discord-Einstellungen → Erweitert → Entwicklermodus an, dann
Rechtsklick auf den Server → „Server-ID kopieren").

### 3. Starten

```bash
docker compose up -d --build
docker compose logs -f
```

Läuft. In Discord jetzt `/watch add` tippen.

---

## Web-Panel

Suchen im Browser verwalten statt per Datei oder Slash-Command. Alerts kommen
weiterhin ausschließlich in Discord an — das Panel zeigt an und verwaltet.

Es läuft im selben Prozess wie der Sniper und teilt sich Datenbank und Monitor:
was du dort änderst, wirkt **sofort**, ohne Neustart. In beiden Betriebsarten
verfügbar.

**Einrichten:** In der `.env` ein Passwort und die Domain setzen …

```
PANEL_PASSWORD=ein-langes-passwort
PANEL_DOMAIN=vinted.example.de
```

… und einen DNS-A-Eintrag für die Domain auf den Server zeigen lassen. Wie es
dann weitergeht, hängt davon ab, ob auf dem Server schon ein Reverse-Proxy die
Ports 80 und 443 belegt (`docker ps` verrät es).

**Fall A — der Server gehört dem Sniper allein.** Dann bringt der Stack seinen
eigenen Caddy mit:

```bash
docker compose --profile standalone up -d --build
```

Caddy besorgt das HTTPS-Zertifikat automatisch und erneuert es selbst.

**Fall B — es läuft schon ein Reverse-Proxy (Caddy, nginx, Traefik).** Zwei
Webserver können sich Port 443 nicht teilen, ein zweiter Caddy käme also gar
nicht erst hoch. Der Sniper-Stack bleibt trotzdem vollständig eigenständig —
eigene Container, eigenes Netz, eigene Volumes, eigener Neustart-Zyklus. Nur
die TLS-Terminierung übernimmt der vorhandene Proxy:

```bash
docker compose up -d --build        # ohne --profile, also ohne eigenen Caddy
docker network connect vinted-sniper-net <name-des-proxy-containers>
```

Der Sniper ist danach unter `vinted-sniper:8080` im Netz `vinted-sniper-net`
erreichbar. Im vorhandenen Caddyfile ein Block dafür, mehr nicht:

```
vinted.example.de {
	reverse_proxy vinted-sniper:8080
}
```

Zusätzlich lauscht der Sniper auf `127.0.0.1:8080` des Hosts — nützlich für
einen Reverse-Proxy, der nicht in Docker läuft, und zum Testen mit
`curl -I http://127.0.0.1:8080/health`. Von außen ist der Port nicht
erreichbar, die Verbindung ins Internet läuft also immer über den Proxy und
damit verschlüsselt.

Ohne `PANEL_PASSWORD` startet das Panel gar nicht erst — sonst könnte jeder die
Suchen ändern, der die Adresse kennt. Die Anmeldung läuft über ein Formular mit
signiertem `SameSite=Strict`-Cookie: damit kann keine fremde Seite Aktionen im
Panel auslösen, während du angemeldet bist.

---

## Befehle (nur Bot-Modus)

| Befehl | Wirkung |
| --- | --- |
| `/watch add url: [name] [channel] [interval]` | Suche anlegen. Testet die URL sofort live und meldet, wenn etwas nicht passt. |
| `/watch bulk [channel] [interval]` | Öffnet ein Eingabefeld für viele Such-URLs auf einmal — eine je Zeile. |
| `/watch test url:` | Such-URL ausprobieren, ohne etwas anzulegen — zeigt die 3 neuesten Treffer als Vorschau (nur für dich sichtbar). |
| `/watch list` | Alle Suchen des Servers mit Status, Intervall, Trefferzahl. |
| `/watch pause id:` / `/watch resume id:` | Suche anhalten bzw. fortsetzen. |
| `/watch interval id: seconds:` | Prüfintervall ändern. |
| `/watch remove id:` | Suche löschen. |
| `/watch import [channel]` | Suchen aus `searches.toml` übernehmen — sie melden danach über den Bot und lassen sich per Command verwalten. Trefferhistorie bleibt erhalten. |
| `/status` | Zustand von Bot und Vinted-Sessions. |
| `/clear [anzahl]` | Räumt den Channel auf (Standard 100 Nachrichten, höchstens 1000). Braucht **Nachrichten verwalten**. |

Bei allen `id`-Optionen schlägt Discord die vorhandenen Suchen per Autocomplete
vor. Die `/watch`-Befehle setzen die Berechtigung **Server verwalten** voraus —
das lässt sich in Discord unter *Servereinstellungen → Integrationen* pro Rolle
anpassen.

### Woher kommt die Such-URL?

1. Auf [vinted.de](https://www.vinted.de) die Suche zusammenklicken: Suchbegriff
   eingeben, links Marke, Größe, Zustand, Preis setzen.
2. Wenn die Ergebnisliste passt: komplette Adresszeile kopieren.
3. Einfügen — im Bot-Modus als `/watch add url:<einfügen>`, im Webhook-Modus
   als `url = "<einfügen>"` in `searches.toml`.

### Viele Suchen auf einmal

Für mehr als eine Handvoll lohnt der Sammel-Import: im Panel unter **Mehrere auf
einmal**, in Discord über `/watch bulk`. Beide erwarten dasselbe Format — eine
Adresse je Zeile:

```
https://www.vinted.de/catalog?search_text=nike+air+max
https://www.vinted.de/catalog?search_text=carhartt&price_to=40

# Zeilen mit Raute werden übersprungen
Stone Island FR | https://www.vinted.fr/catalog?search_text=stone+island
```

Ein Name lässt sich mit `Name | Adresse` voranstellen; ohne wird der Suchbegriff
genommen. Leerzeilen, doppelte Adressen und bereits angelegte Suchen werden
übersprungen, kaputte Zeilen einzeln gemeldet — der Rest läuft trotzdem durch.

Anders als beim einzelnen Hinzufügen wird dabei **nicht** jede Adresse sofort
live geprüft: bei fünfzig Zeilen wären das fünfzig Abfragen auf einen Schlag,
und genau dafür sperrt Vinted zuverlässig. Taugt eine Suche nicht, steht sie
nach dem ersten Durchlauf mit Fehler in der Übersicht.

Der Parser übernimmt `search_text`, `catalog_ids`, `brand_ids`, `size_ids`,
`status_ids`, `color_ids`, `material_ids`, `price_from`, `price_to` und
`currency`. Tracking-Kram (`utm_*` & Co.) wird verworfen. Die Sortierung wird
immer auf *neueste zuerst* gezwungen — alles andere wäre für einen Sniper
nutzlos.

### Was der erste Durchlauf macht

Beim Anlegen liest der Sniper den aktuellen Bestand ein und meldet ihn
**nicht** — sonst würdest du bei jeder neuen Suche sofort 20 Alerts für
Altbestand bekommen. Ab dem zweiten Durchlauf kommt nur noch, was wirklich neu
eingestellt wurde.

Bei einem Neustart passiert das **nicht** noch einmal: welche Artikel schon
gemeldet wurden, steht in der Datenbank. Eine Suche, die es vorher schon gab,
meldet nach dem Neustart sofort wieder normal.

---

## Andere Länderdomains

Einfach die URL der jeweiligen Domain einwerfen — Währung und Sprache erkennt
der Sniper selbst. Mehrere Länder parallel sind kein Problem:

```
/watch add url:https://www.vinted.fr/catalog?search_text=carhartt&price_to=40
/watch add url:https://www.vinted.pl/catalog?search_text=nike&price_to=150
```

Im Webhook-Modus dasselbe in `searches.toml`:

```toml
[[search]]
name = "Carhartt FR"
url = "https://www.vinted.fr/catalog?search_text=carhartt&price_to=40"

[[search]]
name = "Nike PL"
url = "https://www.vinted.pl/catalog?search_text=nike&price_to=150"
```

Bekannt sind `de, at, fr, be, lu, nl, es, it, pt, ie, gr, fi, sk, lt, co.uk, pl,
cz, se, dk, ro, hu, com`. Eine unbekannte `vinted.<tld>` funktioniert auch, sie
fällt dann auf EUR zurück. Jede Domain bekommt ihre eigene Session — die Cookies
werden nicht vermischt.

---

## Antibot: was der Bot tut, wenn Vinted dichtmacht

Vinted sitzt hinter Cloudflare und Datadome. Einen dauerhaften „einmal umgangen,
nie wieder geblockt"-Trick gibt es nicht — der Schutz ändert sich laufend. Der
Ansatz hier ist deshalb: **nicht auffallen und sich selbst reparieren**.

| Mechanismus | Zweck |
| --- | --- |
| **TLS-Impersonation** (`curl_cffi`) | Die Verbindung sieht auf TLS- (JA3) und HTTP/2-Ebene aus wie echtes Chrome, nicht nur im User-Agent. Das ist der Punkt, an dem naive Scraper sofort auffliegen. |
| **Session-Bootstrap** | Vor dem ersten API-Call wird die normale Startseite geladen, damit die Cookies existieren, die die API erwartet. |
| **Token-Refresh** | Bei HTTP 401 wird das Token über den regulären Web-Endpunkt erneuert, statt eine komplett neue Session aufzumachen. |
| **Playwright-Fallback** | Scheitert der HTTP-Bootstrap an einer JS-Challenge, startet ein echter Headless-Chromium, löst sie und übergibt die Cookies an die HTTP-Session. |
| **Proxy-Rotation** | Bei 403/429 wird die Ausgangs-IP gewechselt (sofern `PROXIES` gesetzt ist). |
| **Backoff & Jitter** | Nach Blockaden wird exponentiell zurückgefahren; jedes Intervall bekommt einen Zufallsanteil, damit die Abfragen kein metronomisches Muster bilden. |
| **Rate-Limit pro Domain** | Alle Suchen einer Domain teilen sich ein gemeinsames Budget — zehn Suchen auf vinted.de feuern nicht zehnmal so viel. |

Es gibt keinen Zustand, in dem der Sniper dauerhaft stillsteht: jede Blockade
löst einen Reparaturpfad aus. Hängt eine Suche fünfmal am Stück, meldet sich der
Bot einmal im Channel und wieder, sobald es läuft.

### Erreichbarkeit prüfen

Bevor du im Log rätst, frag den Sniper direkt:

```bash
docker compose run --rm sniper python -m vinted_sniper.check
```

Das prüft die direkte Verbindung und jeden Eintrag aus `PROXIES` einzeln — je
mit ausgehender IP, HTTP-Status und einer Gegenprobe im echten Browser — und
sagt am Ende, welcher Weg funktioniert. Eine andere Länderdomain prüfst du mit
`… vinted_sniper.check vinted.fr`.

### Proxys: worauf es ankommt

Auf einem Server ist die eigene IP fast immer gesperrt; Vinted blockt
Rechenzentren pauschal. Dann führt kein Weg an einem Proxy vorbei — aber am
richtigen:

* **Residential oder ISP/Static-Residential.** Datacenter-Proxys stehen in
  denselben Sperrlisten wie der Server selbst und ändern gar nichts.
* **Feste IPs statt rotierender.** Vinted bindet Session-Cookies an die IP.
  Wechselt sie mitten in der Sitzung, ist genau das das Bot-Muster, nach dem
  Datadome sucht. Zwei bis drei feste IPs sind besser als ein rotierender Pool
  — der Sniper schaltet bei einer Blockade selbst auf die nächste weiter.
* **Passendes Land.** Für `vinted.de` eine deutsche IP: der Browser meldet
  deutsche Sprache und Zeitzone, eine IP aus Übersee widerspricht dem.
* **Monatspreis statt Gigabyte-Abrechnung.** Im Browser-Modus kommen schnell
  über zehn Gigabyte im Monat zusammen.

Traffic senkst du wirksam über das Intervall: 180 statt 60 Sekunden drittelt
ihn, und die wenigsten Schnäppchen sind nach drei Minuten weg.

### Wenn es trotzdem klemmt

1. **Intervall hoch.** 60s statt 20s. Aggressives Polling ist der häufigste
   Grund für Sperren und bringt real kaum Vorsprung.
2. **`IMPERSONATE` wechseln.** `chrome131`, `chrome120` oder `safari17_0` in der
   `.env` probieren.
3. **Proxies eintragen.** Wohn-IPs (Residential) funktionieren deutlich besser
   als Rechenzentrums-IPs:
   ```
   PROXIES=http://user:pass@proxy1:8000,http://user:pass@proxy2:8000
   ```
4. **`PLAYWRIGHT_FALLBACK=true` lassen.** Ohne den Browser-Notnagel steht der Bot
   bei einer JS-Challenge still.
5. **`/status`** zeigt pro Domain, ob die Session steht oder blockiert ist.

---

## Konfiguration

Alles über `.env` (siehe `.env.example`). Mindestens eines von `ALERT_WEBHOOK_URL` oder `DISCORD_TOKEN` muss gesetzt sein:

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `ALERT_WEBHOOK_URL` | — | Webhook-URL für Alerts. Ohne `DISCORD_TOKEN` ist das der Betriebsmodus. |
| `DISCORD_TOKEN` | — | Bot-Token. Gesetzt = Slash-Commands. |
| `DISCORD_GUILD_ID` | — | Optional. Leer = Befehle werden auf allen Servern des Bots registriert. |
| `SEARCHES_PATH` | `searches.toml` | Suchdefinitionen für den Webhook-Modus. |
| `PANEL_PASSWORD` | — | Passwort fürs Web-Panel. Leer = Panel bleibt aus. |
| `PANEL_DOMAIN` | — | Domain fürs Panel. Nur für den mitgelieferten Caddy (`--profile standalone`); ein vorhandener Reverse-Proxy kennt seine Domain selbst. |
| `PANEL_PORT` | `8080` | Port auf `127.0.0.1` des Hosts. Nur bei Konflikten ändern. |
| `DEFAULT_INTERVAL` | `60` | Prüfintervall neuer Suchen (Sekunden). |
| `MIN_INTERVAL` | `20` | Untergrenze, die `/watch interval` nicht unterschreitet. |
| `PER_PAGE` | `20` | Artikel pro Abfrage. |
| `JITTER` | `0.25` | Zufallsanteil auf jedes Intervall (±25 %). |
| `MAX_ITEM_AGE` | `900` | Artikel, die älter sind, werden nicht gemeldet. `0` = aus. |
| `IMPERSONATE` | `chrome124` | Browser-Profil für die TLS-Impersonation. |
| `PROXIES` | — | Komma-getrennte Proxy-Liste für wenige Einträge. |
| `PROXIES_FILE` | `proxies.txt` | Datei mit einer Proxy-Zeile je Zeile — für große Anbieterlisten. |
| `PLAYWRIGHT_FALLBACK` | `true` | Headless-Chromium als Notnagel bei Challenges. |
| `RATE_LIMIT_PER_DOMAIN` | `60` | Requests pro Minute und Domain über alle Suchen. |
| `REQUEST_TIMEOUT` | `20` | Sekunden bis Abbruch einer Anfrage. |
| `DB_PATH` | `data/sniper.db` | SQLite-Datei. |
| `LOG_LEVEL` | `INFO` | `DEBUG` für die Fehlersuche. |

---

## Betrieb ohne Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium      # nur für den Fallback nötig
python -m vinted_sniper       # Modus ergibt sich aus der .env
```

Als systemd-Unit (`/etc/systemd/system/vinted-sniper.service`):

```ini
[Unit]
Description=Vinted Sniper
After=network-online.target

[Service]
WorkingDirectory=/opt/vintedsniper
ExecStart=/opt/vintedsniper/.venv/bin/python -m vinted_sniper
Restart=always
RestartSec=10
User=sniper

[Install]
WantedBy=multi-user.target
```

---

## Aufbau

```
vinted_sniper/
├── config.py            Einstellungen aus der .env, Moduswahl
├── db.py                SQLite: Suchen + bereits gemeldete Artikel
├── monitor.py           Polling-Schleife, ein Task pro Suche
├── searches.py          searches.toml lesen und mit der DB abgleichen
├── notifiers.py         Alert-Zustellung per Webhook
├── panel/
│   ├── app.py           Webserver und Routen
│   ├── auth.py          Anmeldung (signiertes Cookie)
│   └── views.py         HTML der beiden Seiten
├── runner.py            Webhook-Modus (ohne Bot-Token)
├── embeds.py            Discord-Darstellung (beide Modi)
├── vinted/
│   ├── domains.py       Länderdomains, Währungen, Host-Prüfung
│   ├── urls.py          Such-URL → API-Parameter
│   ├── session.py       Antibot-Handling, Sessions, Reparaturpfade
│   ├── client.py        Katalog-Abfrage
│   └── models.py        Artikel-Datenmodell
└── bot/
    ├── bot.py           Bot-Klasse, Alert-Zustellung
    └── commands.py      Slash-Commands
```

Die Daten liegen in `data/sniper.db` (per Volume gemountet) und überleben jeden
Neustart — laufende Suchen werden beim Start automatisch wieder aufgenommen.
Gemeldete Artikel-IDs werden nach sieben Tagen aufgeräumt.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Deckt URL-Parsing, Domain- und Host-Prüfung, das Artikel-Modell, das Lesen von
`searches.toml` sowie Datenbank und Datei-Abgleich ab — also die Logik, die
ohne Netzwerkzugriff prüfbar ist. Die Datenbanktests laufen gegen einen
schlanken `aiosqlite`-Ersatz auf `sqlite3`-Basis (`tests/aiosqlite_stub.py`).

## Hinweise

- Der Bot liest ausschließlich öffentlich sichtbare Angebote und kauft nichts
  automatisch. Der „Sofort kaufen"-Button ist ein normaler Link, den du selbst
  anklickst.
- Halte die Intervalle moderat. Wer im Sekundentakt pollt, wird gesperrt und hat
  am Ende weniger Alerts, nicht mehr.
- Vinteds interne API ist nicht dokumentiert und kann sich jederzeit ändern.
  Wenn Abfragen plötzlich fehlschlagen, liegt es meist daran — `LOG_LEVEL=DEBUG`
  zeigt, was zurückkommt.
