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

**Mehrere Suchen auf einmal:** Über der Liste sitzt eine Leiste mit
Ankreuzfeldern. *alle*, *keine* oder ein Land anklicken, dann **Pausieren**,
**Fortsetzen** oder **Löschen** — bei einundzwanzig Suchen aus sieben Ländern
ist alles andere Klickarbeit. Ohne Javascript kreuzt man von Hand an und alles
funktioniert weiterhin; das kurze Skript fügt nur die Sammelauswahl hinzu.

Gelöschte Suchen aus `searches.toml` legt der nächste Start neu an — sollen sie
weg bleiben, gehören sie auch dort heraus. Das Panel sagt das nach dem Löschen
noch einmal dazu.

Ohne `PANEL_PASSWORD` startet das Panel gar nicht erst — sonst könnte jeder die
Suchen ändern, der die Adresse kennt. Die Anmeldung läuft über ein Formular mit
signiertem `SameSite=Strict`-Cookie: damit kann keine fremde Seite Aktionen im
Panel auslösen, während du angemeldet bist.

---

## Ohne eigenes Abfragen betreiben

Vinted untersagt in seinen [AGB](https://www.vinted.com/terms-and-conditions)
externe Bots und Scraper ohne ausdrückliche Erlaubnis. Ein längeres Intervall
ändert daran nichts — es macht das Abfragen langsamer, nicht erlaubt. Es gibt
keine Einstellung, bei der automatisches Abfragen unbedenklich wäre; wer es
betreibt, trägt das Risiko für sein Konto selbst.

Der Sniper lässt sich deshalb auch ohne eigenes Abfragen betreiben:

```
POLLING=off
```

Damit geht **keine einzige Anfrage** an Vinted raus. Was weiterläuft: das
Panel, die Kaufprofile, die Ampel, die Preisreferenz, die Entdopplung und
Discord. Der Weg, wie Treffer hereinkommen, ändert sich:

1. Filter **auf Vinted** als gespeicherte Suche anlegen
2. Vinteds eigene Benachrichtigungen einschalten
3. Bei einem Treffer die Eckdaten in `/pruefen` eingeben
4. Prüfen und selbst kaufen

`/pruefen` macht dabei die Arbeit, die den Unterschied ausmacht:

```
/pruefen titel:Polo Ralph Lauren Quarter Zip Navy preis:9 groesse:L zustand:Sehr gut
```

> 🟢 **GRÜN — kaufen** · RL Quarter/Half Zip
> ≈ **+14,86 € Gewinn** bei 13,14 € Gesamt-EK · 113 % ROI
> **Dein maximaler Artikelpreis: 10,00 €**
> ☐ Checkout-Preis ansehen — der zählt, nicht der Artikelpreis
> ☐ Pflegeetikett vorhanden, möglichst 100 % Baumwolle …

Mit `checkout:` gibst du den echten Betrag aus dem Vinted-Checkout an; ohne
ihn wird mit geschätzten Versand- und Käuferschutzkosten gerechnet und der
Alert sagt das dazu.

Was in dieser Betriebsart schwächer wird: Die Preisreferenz (`MIN_DISCOUNT`)
speist sich aus den Angeboten, die der Sniper sieht — ohne eigenes Abfragen
sieht er fast keine. Der Median taugt dann wenig, und die eBay-Recherche nach
tatsächlich verkauften Artikeln wird zur eigentlichen Grundlage. Der Link
dafür hängt an jedem Ergebnis.

---

## Nicht rund um die Uhr

Nachts wird auf Vinted wenig eingestellt, und wer um drei Uhr einen Alert
bekommt, schläft. Jede Abfrage in der Zeit kostet trotzdem Proxy-Volumen.

```
ACTIVE_HOURS=08:00-23:00
TIMEZONE=Europe/Berlin
```

Außerhalb des Fensters geht keine einzige Anfrage raus. Die Suchen bleiben
angelegt, im Panel steht „Nachtruhe" statt „läuft", der Wachhund schlägt
nicht Alarm, und um acht geht es weiter. Das Fenster darf über Mitternacht
gehen (`22:00-02:00`). Die Volumen-Hochrechnung rechnet mit dem Fenster: bei
15 von 24 Stunden sind es 15 von 24 Stunden.

Was zwischen 23 und 8 Uhr eingestellt wird, sieht der Sniper nicht — auch
nicht um acht nachträglich, denn Artikel älter als `MAX_ITEM_AGE` werden
nicht gemeldet. Das ist Absicht: ein Fund von vier Uhr nachts ist um acht
meist weg.

---

## Proxy-Volumen im Blick behalten

Beim Proxy zahlt man nach übertragenen Bytes, nicht nach Abfragen. Ist das
Kontingent leer, liefern **alle** Proxys HTTP 402 — auch tausend Sitzungen
ändern daran nichts, denn das Kontingent gilt fürs Konto. Der Sniper steht
dann, ohne dass vorher etwas darauf hingedeutet hätte.

Deshalb misst er mit. `/status` und das Panel zeigen:

```
heute 412,3 MB in 14 208 Abfragen (Ø 29,7 KB) · insgesamt 2,1 GB
Hochgerechnet: 12,4 GB in 30 Tagen bei 35 Suchen alle 60s
```

Die Hochrechnung ist die Zahl, die vor dem Nachkaufen zählt. Gemessen wird der
Rumpf der Antwort; Header und TLS kommen beim Anbieter obendrauf, die
Abrechnung liegt also etwas darüber.

**Die vier Stellschrauben**, absteigend nach Wirkung:

| Maßnahme | Wirkung |
| --- | --- |
| Weniger Länder (`EXTRA_COUNTRIES`) | linear — von 7 auf 3 spart 57 % |
| Längeres Intervall | linear — 60s → 120s halbiert |
| `PER_PAGE=10` statt `20` | grob halbe Antwortgröße |
| Suchen pausieren, die nichts liefern | siehe Trefferzahl im Panel |

Ein Rechenbeispiel bei ~25 KB je Abfrage:

| Konfiguration | pro Tag | pro Monat |
| --- | --- | --- |
| 35 Suchen, alle 60 s | 1,2 GB | 36 GB |
| 35 Suchen, alle 120 s | 615 MB | 18 GB |
| 15 Suchen, alle 60 s | 527 MB | 15 GB |
| 15 Suchen, 120 s, `PER_PAGE=10` | 148 MB | 4,3 GB |

Die 25 KB sind eine Schätzung — **verlass dich auf den gemessenen Wert in
`/status`**, der steht nach einer Stunde Betrieb.

Der Browser-Modus (Notnagel bei Sperren) lädt keine Bilder, Videos und
Schriften mehr. Auf einer Vinted-Katalogseite sind das mehrere Megabyte je
Aufruf; für die Katalogabfrage sind sie wertlos. Javascript bleibt an — daran
hängt die Antibot-Prüfung.

---

## Was gerade gilt, sagt der Sniper selbst

Bei jedem Start schreibt der Sniper einen Bericht in den Discord-Channel und
zeigt ihn im Panel unter „Betrieb": ob abgefragt wird und wie oft, in welchen
Ländern, wie entdoppelt wird, welche Kaufprofile greifen, wer bei einem
Ausfall getaggt wird, ob aufgeräumt wird, wie viele Proxy-Sitzungen laufen,
ob der Totmannschalter an ist. Was man so wahrscheinlich nicht wollte, ist
mit ⚠️ markiert. Steht die Abfrage auf `POLLING=off`, kommt der Bericht als
Ausfallmeldung mit Erwähnung: der Sniper sucht dann nicht, und das soll
niemand erst nach drei stillen Tagen merken.

Niemand muss dafür in die `.env` schauen oder einen Befehl eingeben.

---

## Merken, wenn der Sniper ausfällt

Ein Sniper, der still ausfällt, ist schlimmer als keiner: man verlässt sich auf
ihn und merkt tagelang nicht, dass nichts mehr kommt. Drei Stufen decken
unterschiedliche Ausfälle ab — **keine einzelne deckt alle ab**.

```
ALERT_MENTION=123456789012345678
```

Das ist deine Discord-Benutzer-ID, nicht dein Name: Einstellungen → Erweitert →
Entwicklermodus einschalten, dann Rechtsklick auf deinen Namen →
*Benutzer-ID kopieren*. Über den Namen pingt Discord nicht.

**Stufe 1 — die Suchen hängen.** Der Prozess läuft, aber keine Abfrage kommt
mehr durch: Proxy-Kontingent leer, IP gesperrt, Vinted hat etwas geändert. Der
Bot prüft das alle fünf Minuten und meldet es mit Erwähnung. Geprüft wird nicht
nur auf Fehler, sondern auch auf Schweigen — eine Suche, die gar nicht mehr
durchläuft, setzt nämlich auch keinen Fehler. Gepingt wird nur, wenn **alle**
Suchen betroffen sind; eine einzelne hakende meldet der Monitor ohnehin.

**Stufe 2 — der Prozess war weg.** Absturz, Neustart, Server rebootet. Der
Sniper schreibt jede Minute ein Lebenszeichen in die Datenbank und vergleicht
es beim Start mit der Uhr. War er länger als drei Minuten weg, meldet er die
Lücke mit Dauer.

**Stufe 3 — der Server ist aus.** Das kann der Bot grundsätzlich nicht melden,
er läuft ja nicht. Dafür der Totmannschalter: er pingt jede Minute eine fremde
URL an, und *das Ausbleiben* dieses Pings löst dort den Alarm aus.

```
HEARTBEAT_URL=https://hc-ping.com/dein-schluessel
```

Kostenlos mit [healthchecks.io](https://healthchecks.io): Konto anlegen, *Add
Check*, Periode 5 Minuten, Grace 5 Minuten, Ping-URL hier eintragen. Unter
*Integrations* Discord verbinden — dann schreibt der Dienst selbst in deinen
Channel, auch wenn dein Server komplett aus ist.

Ohne Stufe 3 bleibt genau ein Ausfall unbemerkt: der, bei dem nichts mehr läuft,
was reden könnte. Die fünf Minuten Einrichtung lohnen sich.

---

## Befehle (nur Bot-Modus)

| Befehl | Wirkung |
| --- | --- |
| `/watch add url: [name] [channel] [interval] [laender]` | Suche anlegen. Testet die URL sofort live und meldet, wenn etwas nicht passt. `laender: fr, nl, it` legt sie zusätzlich dort an. |
| `/watch bulk [channel] [interval] [laender]` | Öffnet ein Eingabefeld für viele Such-URLs auf einmal — eine je Zeile. |
| `/watch test url:` | Such-URL ausprobieren, ohne etwas anzulegen — zeigt die 3 neuesten Treffer als Vorschau (nur für dich sichtbar). |
| `/watch list` | Alle Suchen des Servers mit Status, Intervall, Trefferzahl. |
| `/watch pause id:` / `/watch resume id:` | Suche anhalten bzw. fortsetzen. |
| `/watch interval id: seconds:` | Prüfintervall ändern. |
| `/watch remove id:` | Suche löschen. |
| `/watch import [channel]` | Suchen aus `searches.toml` übernehmen — sie melden danach über den Bot und lassen sich per Command verwalten. Trefferhistorie bleibt erhalten. |
| `/pruefen titel: preis: [groesse] [zustand] [checkout] [url]` | Rechnet einen Fund durch: Ampel, Marge, ROI, **maximaler Einkaufspreis**. Fragt Vinted **nicht** ab. |
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

### Kaufprofile: melden, was sich rechnet

Der Median sagt „günstiger als der Rest". Fürs Weiterverkaufen zählt eine
andere Zahl: **was bleibt nach allen Kosten übrig?** Ein Fund für 10 € kann mit
Versand und Käuferschutz bereits zu teuer sein.

`profiles.toml` beschreibt, wonach du wirklich suchst und was es einbringen
muss:

```bash
cp profiles.example.toml profiles.toml
```

Der Sniper rechnet dann jeden Fund durch und vergibt eine Ampel:

| | |
| --- | --- |
| 🟢 **grün** | über der Gewinnschwelle, gefragte Größe und Farbe, bester Zustand |
| 🟡 **gelb** | kaufbar, aber erst nachfragen: XL, Zustand „gut", Farbe unklar, knappe Marge |
| 🔴 **rot** | unter der Schwelle, falsche Größe, Ausschlusswort im Titel — kommt gar nicht erst in den Channel |

Gerechnet wird so:

```
Gesamt-EK = Artikel + Versand + Käuferschutz + Aufbereitung
Gewinn    = konservativer VK − Gesamt-EK − Reserve
ROI       = Gewinn / Gesamt-EK
```

Jeder Alert zeigt die **Rechnung** dazu, Posten für Posten:

```
9,00 € Artikel + 2,99 € Versand (geschätzt) + 1,10 € Käuferschutz (laut Vinted) = 13,09 € Gesamt-EK
31,00 € VK − 13,09 € − 3,00 € Reserve = +14,91 € Gewinn (114 % ROI)
```

Dazu Gewinn, Gesamt-EK und ROI in der Kopfzeile, die 90-Sekunden-Prüfliste und
der Link auf die eBay-Suche nach **tatsächlich verkauften** Vergleichsartikeln.
Bei Gelb liegt die Nachfrage an den Verkäufer fertig zum Kopieren dabei.

**Woher die Zahlen kommen.** Alle Beträge werden kaufmännisch auf den Cent
gerundet, bevor verglichen wird — eine Marge von 11,999 € ist keine 12 €, aber
auch kein Rundungszufall. Die Quellen, verlässlichste zuerst:

1. **Checkout-Betrag** — `/pruefen … checkout:` mit dem im Vinted-Checkout
   abgelesenen Gesamtbetrag. Dann wird nichts mehr geschätzt.
2. **Käuferschutz laut Vinted** — die Angebotsdaten enthalten meist den Preis
   inklusive Käuferschutz. Der wird genommen; geschätzt wird nur noch der
   Versand. Im Alert steht dann „laut Vinted".
3. **Alles geschätzt** — nach den Sätzen in `profiles.toml`. Im Alert steht
   „geschätzt".

**Andere Währungen.** Ein Fund aus Großbritannien oder Polen kommt in GBP oder
PLN. Er wird mit den Kursen aus `[rates]` in die Profilwährung umgerechnet,
und die Rechnung zeigt es: „8,00 GBP × 1.17 = 9,36 € Artikel". Fehlt der Kurs,
wird nicht geraten: der Fund bekommt Rot mit dem Hinweis, welcher Kurs fehlt.
Für die EUR-Länder stellt sich die Frage nicht.

**Drei Dinge, die die Ampel nicht kann.** Der Versand bleibt eine Schätzung,
bis du ihn im Checkout siehst — und der Checkout gehört vor jedem Kauf
angesehen. Echtheit, Pflegeetikett, Löcher und ausgeleierte Bündchen sieht
nur ein Mensch auf den Fotos. Und ob ein Verkäufer zehn angeblich neue Teile
derselben Marke anbietet, steht nicht in den Angebotsdaten. Die Ampel sagt
„hinschauen lohnt sich", nicht „kaufen".

**Der Sniper kauft nichts.** Er meldet, sonst nichts — und das bleibt so.

### Nur melden, was Geld wert ist

Ein Sniper, der jeden neuen Treffer meldet, produziert vor allem Rauschen.
Fürs Weiterverkaufen zählt nicht „neu", sondern „deutlich unter dem, was
Vergleichbares kostet".

Die Vergleichsbasis entsteht nebenbei: bei jedem Durchlauf sieht der Sniper
ohnehin die aktuellen Angebote einer Suche. Ihre Preise werden gesammelt, und
jeder neue Fund wird gegen den Median gehalten. Der Alert zeigt das Ergebnis
mit — „**38 % unter** Median (40 €, 217 Vergleiche)" — und färbt sich grün,
wenn der Fund darunter liegt.

```
MIN_DISCOUNT=30
```

Ab dann kommen nur noch Funde durch, die mindestens 30 % unter dem Median
liegen. Bis 25 Vergleichswerte beisammen sind, wird nur gesammelt und alles
gemeldet — ein Sniper, der aus Datenmangel schweigt, ist schlimmer als einer,
der zu viel meldet.

**Was der Median ist und was nicht:** Er ist der *Angebotspreis* vergleichbarer
Artikel, nicht der erzielte Verkaufspreis — Verkaufspreise gibt Vinted nicht
heraus. „30 % unter Median" heißt „deutlich günstiger als das, was gerade sonst
angeboten wird". Das ist ein brauchbarer Näherungswert, keine Gewinngarantie.

**Und er taugt nur so viel, wie die Suche eng ist.** `search_text=nike` mischt
Socken mit Sneakern; der Median daraus sagt nichts. `Nike Air Max 90` mit Größe
und Zustand liefert eine Zahl, auf die man sich verlassen kann. Eine enge Suche
ist hier mehr wert als jede Einstellung.

Dazu drei Filter gegen offensichtlichen Ausschuss:

```
MIN_PRICE=5
EXCLUDE_WORDS=defekt,kaputt,riss,fleck,fake,replica,nachbau,bitte lesen
REQUIRE_PHOTO=true
```

### Brauchst du die Länderkopien überhaupt?

Vinted zeigt im deutschen Katalog längst auch Artikel aus anderen Ländern,
sofern der Verkäufer international versendet. Wie viel davon greift, hängt an
Kategorie und Verkäufer — pauschal beantworten lässt sich das nicht.

Deshalb misst der Sniper es an deinen eigenen Daten. Jede Suche zählt mit, wie
oft sie etwas fand, das eine Schwestersuche **schon hatte**:

```
#12 · Quarter Zip 🇫🇷    alle 60s · 3 Treffer · 47 doppelt
```

Drei eigene Funde gegen siebenundvierzig Wiederholungen: die französische Kopie
trägt fast nichts bei und kostet trotzdem ein Siebtel deines Proxy-Volumens.
Weg damit.

```
#15 · Quarter Zip 🇮🇹    alle 60s · 21 Treffer · 4 doppelt
```

Umgekehrt: Italien findet Ware, die in Deutschland nicht auftaucht. Die Kopie
lohnt sich.

Nach ein, zwei Tagen steht in der Liste, welche Länder tragen und welche nur
Volumen kosten. Danach im Panel die Häkchen setzen und pausieren — die
Sammelauswahl kann nach Land filtern.

### Dieselbe Suche in mehreren Ländern

Derselbe Artikel kostet in Frankreich oder Italien oft deutlich weniger als in
Deutschland — wer nur `.de` beobachtet, sieht ihn nie.

**Automatisch für alles**, in der `.env`:

```
EXTRA_COUNTRIES=fr,nl,it,es,be,at
```

Ab dann läuft jede Suche zusätzlich in diesen Ländern — auch die, die schon
angelegt sind: beim nächsten Start werden die fehlenden Kopien ergänzt. Nimmst
du ein Land wieder heraus, verschwinden dessen Suchen beim nächsten Start
wieder. Im Panel sind die eingestellten Länder vorausgewählt, in Discord gilt
`EXTRA_COUNTRIES`, wenn du bei `/watch add` nichts angibst (`laender: -`
schaltet es für einen Aufruf ab).

**Oder von Fall zu Fall**: im Panel die Ankreuzfelder unter dem Adressfeld, in
Discord `laender: fr, nl, it`.

Aus einer Adresse werden dann mehrere Suchen — eine je Land, mit denselben
Filtern. Erkannt werden alle 22 Vinted-Länder, in jeder Schreibweise: `fr`,
`.fr`, `vinted.fr`, `www.vinted.fr`; `uk` steht für `co.uk`. Was nicht
zuzuordnen ist, wird gemeldet statt stillschweigend zu einer erfundenen Domain
zu werden.

Zwei Dinge, die dabei wichtig sind:

- **Jeder Artikel wird trotzdem nur einmal gemeldet.** Vinted vergibt
  Artikel-IDs länderübergreifend: derselbe Artikel hat auf `.de` und `.fr`
  dieselbe ID, und ohne Gegenmaßnahme meldete ihn jede Länderkopie einzeln —
  bei sieben Ländern also siebenmal. Und wer drei sich überschneidende Suchen
  hat („Quarter Zip", „Half Zip", „Ralph Lauren Pullover"), bekäme ihn noch
  dreimal öfter. Deshalb gilt standardmäßig `DEDUPE_SCOPE=all`: ein Artikel,
  ein Alert, egal aus wie vielen Suchen und Ländern. Wer zuerst hinschaut,
  meldet. `group` beschränkt das auf die Länderkopien derselben Suche,
  `watch` schaltet es ab.
- **Auch unter neuer ID nicht.** Verkäufer löschen Artikel und stellen sie neu
  ein, um oben zu landen, oder legen denselben Pullover zweimal an — neue ID,
  gleicher Artikel. Der Sniper erkennt das an Verkäufer, Titel, Größe und
  Preis und meldet nicht noch einmal, in jedem Modus. Nur wenn der Preis
  gesunken ist, kommt der Fund wieder durch: das ist eine Nachricht wert.
- **Im Log steht, was gilt.** Beim Start meldet der Sniper
  `Entdopplung: Modus all — 18 Suche(n) in 3 Gruppe(n)`. Wer trotzdem
  Doppel-Alerts sieht, schaut zuerst dorthin: `docker compose logs | grep
  Entdopplung`. Im Panel zeigt jede Suche „N doppelt" — so viel hat sie
  gefunden, das eine andere schon gemeldet hatte.
- **Preisfilter hängen an der Währung.** Kategorie-, Marken- und Größen-IDs
  sind bei Vinted länderübergreifend dieselben, die Währung nicht. Geht eine
  Suche mit Preisgrenze nach Polen oder Großbritannien, wird die ursprüngliche
  Währung ausdrücklich mitgegeben, damit aus „bis 40 EUR" nicht klammheimlich
  „bis 40 PLN" wird — und du bekommst einen Hinweis dazu. Bei den EUR-Ländern
  (fr, nl, it, es, be, at …) stellt sich die Frage nicht.
- **Jedes Land fragt eigenständig ab.** Fünf Länder bei drei Suchen sind
  achtzehn Suchen. Die verteilen sich zwar auf sechs Domains, trotzdem gilt:
  je mehr Länder, desto länger sollte das Intervall sein. 60 Sekunden sind
  vernünftig, 20 bei achtzehn Suchen nicht.

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
| `EXTRA_COUNTRIES` | — | Länder, in denen **jede** Suche zusätzlich läuft, z. B. `fr,nl,it`. Leer = nur die Domain aus der Such-URL. |
| `DEDUPE_SCOPE` | `all` | Wie weit ein gemeldeter Artikel andere Suchen stummschaltet: `all` = jede Suche und jedes Land, `group` = nur die Länderkopien derselben Suche, `watch` = gar nicht. Neueinstellungen desselben Artikels werden in jedem Modus zusammengefasst. |
| `ALERT_MENTION` | — | Wer bei einem Ausfall angepingt wird. Benutzer-IDs, komma-getrennt, oder `@here`. |
| `HEALTH_CHANNEL` | — | Channel-ID für Ausfallmeldungen. Leer = Channel der ersten Suche bzw. Webhook. |
| `HEALTH_STALE_AFTER` | `900` | Ab wann eine Suche als „meldet nichts mehr" gilt (Sekunden). |
| `HEALTH_EVERY` | `300` | Abstand zwischen zwei Zustandsprüfungen (Sekunden). |
| `HEARTBEAT_URL` | — | Totmannschalter: URL, die jede Minute angepingt wird. Bleibt der Ping aus, meldet der fremde Dienst den Ausfall. |
| `ALERT_RETENTION_HOURS` | `0` | Alerts, die älter sind, werden automatisch gelöscht. `0` = aus. |
| `CLEANUP_CHANNELS` | — | Channel-IDs zum Aufräumen. Leer = die Channels, in die der Bot selbst alertet. |
| `ACTIVE_HOURS` | — | Zeitfenster fürs Abfragen, z. B. `08:00-23:00`. Außerhalb keine Anfragen, kein Volumen. Leer = rund um die Uhr. |
| `TIMEZONE` | `Europe/Berlin` | Zeitzone für `ACTIVE_HOURS`. |
| `DEFAULT_INTERVAL` | `60` | Prüfintervall neuer Suchen (Sekunden). |
| `MIN_INTERVAL` | `20` | Untergrenze, die `/watch interval` nicht unterschreitet. |
| `POLLING` | `on` | `off` stellt jede Anfrage an Vinted ab — Bewertung, Panel und Discord laufen weiter. |
| `PER_PAGE` | `20` | Artikel pro Abfrage. |
| `JITTER` | `0.25` | Zufallsanteil auf jedes Intervall (±25 %). |
| `MAX_ITEM_AGE` | `900` | Artikel, die älter sind, werden nicht gemeldet. `0` = aus. |
| `MIN_DISCOUNT` | `0` | Nur melden, was mindestens so viel Prozent unter dem Median vergleichbarer Angebote liegt. `0` = alles melden. |
| `PRICE_WINDOW_DAYS` | `30` | Zeitfenster der Vergleichspreise. |
| `MIN_PRICE` | `0` | Artikel unter diesem Preis überspringen. |
| `EXCLUDE_WORDS` | — | Komma-getrennte Wörter, die einen Artikel am Titel aussortieren. |
| `REQUIRE_PHOTO` | `false` | Artikel ohne Foto überspringen. |
| `IMPERSONATE` | `chrome124` | Browser-Profil für die TLS-Impersonation. |
| `PROXIES` | — | Komma-getrennte Proxy-Liste für wenige Einträge. |
| `PROXIES_FILE` | `proxies.txt` | Datei mit einer Proxy-Zeile je Zeile — für große Anbieterlisten. |
| `PROXIES_TEMPLATE` | — | Vorlage für durchnummerierte Sitzungen, z. B. `p.webshare.io:80:user-DE-{n}:pw`. |
| `PROXIES_SESSIONS` | `0` | Wie viele Sitzungen aus der Vorlage erzeugt werden (`{n}` = 1 … n). |
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
