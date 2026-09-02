"""HTML für das Panel.

Bewusst ohne Template-Engine und ohne Frontend-Framework: es sind zwei Seiten,
und ein Build-Schritt für Javascript würde den Betrieb nur verkomplizieren. Die
Formulare sind gewöhnliches HTML, das ohne Javascript funktioniert.

Jeder Wert, der aus der Datenbank oder von Vinted kommt, läuft durch
`html.escape` — Suchnamen und Titel sind Nutzereingaben.
"""

from __future__ import annotations

import datetime as dt
from html import escape

from ..db import Watch
from ..report import Row
from ..vinted import domains

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f4f6f8;
  --bg-soft: #eceff3;
  --card: #ffffff;
  --text: #14171c;
  --muted: #6a7280;
  --line: #e2e6ec;
  --line-soft: #eef1f5;
  --accent: #0aa8b0;
  --accent-soft: #e5f7f8;
  --danger: #d1495b;
  --danger-soft: #fdecee;
  --ok: #2b9e5f;
  --ok-soft: #e7f6ed;
  --warn: #c4761a;
  --shadow: 0 1px 2px rgba(20, 23, 28, .05), 0 4px 16px rgba(20, 23, 28, .05);
  --radius: 14px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1114;
    --bg-soft: #171a1f;
    --card: #1a1d23;
    --text: #e9ecf1;
    --muted: #939aa5;
    --line: #282c34;
    --line-soft: #22262d;
    --accent: #22c3cb;
    --accent-soft: #10292c;
    --danger: #f0748a;
    --danger-soft: #2b1a1f;
    --ok: #4cc484;
    --ok-soft: #14261c;
    --warn: #e0a44e;
    --shadow: 0 1px 2px rgba(0, 0, 0, .3), 0 6px 20px rgba(0, 0, 0, .25);
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  padding: 0 16px 80px;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
        "Helvetica Neue", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 940px; margin: 0 auto; }

/* ------------------------------------------------------------------ Kopf */

.top {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; flex-wrap: wrap;
  padding: 26px 0 22px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 26px;
}
.brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
.mark {
  width: 38px; height: 38px; flex: none;
  display: grid; place-items: center;
  border-radius: 11px;
  background: linear-gradient(140deg, var(--accent), color-mix(in srgb, var(--accent) 55%, #1b6ee0));
  box-shadow: 0 2px 10px color-mix(in srgb, var(--accent) 35%, transparent);
}
.mark svg { display: block; }
.brand h1 { font-size: 17px; font-weight: 650; margin: 0; letter-spacing: -.01em; }
.brand p { margin: 1px 0 0; color: var(--muted); font-size: 13px; }

/* ------------------------------------------------------------- Bausteine */

h2 {
  font-size: 12.5px; font-weight: 650; margin: 30px 0 12px;
  color: var(--muted); text-transform: uppercase; letter-spacing: .07em;
}
h2 .count { color: color-mix(in srgb, var(--muted) 60%, transparent); }

.card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 18px;
  margin-bottom: 12px;
  box-shadow: var(--shadow);
}
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.grow { flex: 1 1 340px; min-width: 0; }

/* ---------------------------------------------------------- Bedienelemente */

input, select, button, textarea {
  font: inherit;
  padding: 10px 13px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: var(--bg-soft);
  color: var(--text);
  transition: border-color .12s ease, background .12s ease, box-shadow .12s ease;
}
input::placeholder, textarea::placeholder { color: color-mix(in srgb, var(--muted) 75%, transparent); }
input:hover, textarea:hover, select:hover { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
input:focus, select:focus, textarea:focus {
  outline: none;
  border-color: var(--accent);
  background: var(--card);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent);
}
textarea {
  width: 100%;
  resize: vertical;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
  font-size: 12.5px; line-height: 1.65;
}

button {
  cursor: pointer;
  border-color: transparent;
  background: var(--accent);
  color: #fff;
  font-weight: 600;
  white-space: nowrap;
}
button:hover { filter: brightness(1.07); }
button:active { transform: translateY(1px); }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
button.ghost {
  background: transparent; border-color: var(--line);
  color: var(--text); font-weight: 500;
}
button.ghost:hover { background: var(--bg-soft); border-color: var(--muted); filter: none; }
button.danger {
  background: transparent; border-color: var(--line);
  color: var(--danger); font-weight: 500;
}
button.danger:hover { background: var(--danger-soft); border-color: var(--danger); filter: none; }
button.small, input.small { padding: 7px 11px; font-size: 13.5px; border-radius: 9px; }

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px; background: var(--bg-soft);
  border: 1px solid var(--line-soft); border-radius: 5px; padding: 1px 5px;
}

/* ------------------------------------------------------------- Kennzahlen */

.stats {
  display: grid; gap: 10px; margin-bottom: 12px;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
}
.stat {
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 15px 17px; box-shadow: var(--shadow);
}
.stat b {
  display: block; font-size: 25px; font-weight: 650;
  letter-spacing: -.02em; line-height: 1.15;
}
.stat span {
  color: var(--muted); font-size: 12.5px;
  text-transform: uppercase; letter-spacing: .05em;
}

/* --------------------------------------------------------------- Sessions */

.sessions { display: flex; flex-direction: column; gap: 8px; }
.session { display: flex; align-items: baseline; gap: 9px; font-size: 13.5px; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); flex: none; }
.session b { font-weight: 600; }
.session span { color: var(--muted); overflow-wrap: anywhere; }

/* ---------------------------------------------------------------- Betrieb */

.betrieb { display: grid; grid-template-columns: max-content 1fr; gap: 7px 14px; font-size: 13.5px; }
.betrieb b { font-weight: 600; white-space: nowrap; }
.betrieb span { color: var(--muted); overflow-wrap: anywhere; }
.betrieb .warn b, .betrieb .warn span { color: var(--danger); }
.betrieb .warn span::before { content: "⚠ "; }

/* ------------------------------------------------------------------ Suchen */

.watch { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
.watch .info { flex: 1 1 340px; min-width: 0; }
.name {
  font-weight: 620; font-size: 15.5px; letter-spacing: -.01em;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}
.name .id { color: var(--muted); font-weight: 500; }
.meta {
  color: var(--muted); font-size: 13px; margin-top: 5px;
  overflow-wrap: anywhere;
}
.meta .sep { opacity: .45; margin: 0 6px; }
.meta a { color: var(--accent); text-decoration: none; }
.meta a:hover { text-decoration: underline; }
.error {
  margin-top: 9px; font-size: 13px; color: var(--danger);
  background: var(--danger-soft); border-radius: 8px;
  padding: 7px 10px; overflow-wrap: anywhere;
}

.pill {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; font-weight: 600; padding: 3px 9px;
  border-radius: 99px; border: 1px solid var(--line);
  color: var(--muted); background: var(--bg-soft);
  text-transform: uppercase; letter-spacing: .04em;
}
.pill.on { color: var(--ok); background: var(--ok-soft); border-color: transparent; }
.pill.err { color: var(--danger); background: var(--danger-soft); border-color: transparent; }

/* ------------------------------------------------------------ Mehrfachwahl */

.toolbar { position: sticky; top: 8px; z-index: 5; padding: 12px 14px; }
.toolbar .spacer { flex: 1 1 auto; }
.chosen { color: var(--muted); font-size: 13px; white-space: nowrap; }
.pick { display: flex; align-items: center; padding-top: 3px; flex: none; }
.pick input {
  width: 18px; height: 18px; margin: 0; padding: 0;
  accent-color: var(--accent); cursor: pointer;
}

.actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.actions form { display: inline-flex; gap: 6px; }
.actions input[type=number] { width: 78px; }

/* ------------------------------------------------------------- Rückmeldung */

.flash {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 13px 15px; border-radius: var(--radius); margin-bottom: 18px;
  background: var(--ok-soft); color: var(--text);
  border: 1px solid color-mix(in srgb, var(--ok) 25%, transparent);
  font-size: 14px;
}
.flash.bad {
  background: var(--danger-soft);
  border-color: color-mix(in srgb, var(--danger) 25%, transparent);
}
.flash .icon { flex: none; font-size: 15px; line-height: 1.5; }

/* ------------------------------------------------------------------ Import */

details.bulk {
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); box-shadow: var(--shadow); margin-bottom: 12px;
}
details.bulk > summary {
  cursor: pointer; padding: 15px 18px; font-weight: 600; font-size: 14px;
  list-style: none; display: flex; align-items: center; gap: 9px;
  border-radius: var(--radius);
}
details.bulk > summary::-webkit-details-marker { display: none; }
details.bulk > summary::before {
  content: "＋"; color: var(--accent); font-weight: 700;
}
details.bulk[open] > summary::before { content: "－"; }
details.bulk > summary:hover { color: var(--accent); }
details.bulk > summary small { color: var(--muted); font-weight: 400; }
details.bulk .body { padding: 0 18px 18px; }

.hint { color: var(--muted); font-size: 13px; margin-top: 11px; }

/* -------------------------------------------------------------- Länderwahl */

fieldset.laender {
  border: none; margin: 14px 0 0; padding: 0;
}
fieldset.laender legend {
  padding: 0 0 8px; color: var(--muted); font-size: 12.5px;
  text-transform: uppercase; letter-spacing: .06em; font-weight: 600;
}
.chips { display: flex; flex-wrap: wrap; gap: 7px; }
.chip { position: relative; }
.chip input {
  position: absolute; inset: 0; opacity: 0; margin: 0;
  width: 100%; height: 100%; cursor: pointer;
}
.chip span {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: 99px;
  border: 1px solid var(--line); background: var(--bg-soft);
  font-size: 13.5px; transition: all .12s ease;
}
.chip input:hover + span { border-color: var(--accent); }
.chip input:checked + span {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}
.chip input:focus-visible + span { outline: 2px solid var(--accent); outline-offset: 2px; }

/* ------------------------------------------------------------------- Login */

.login { max-width: 360px; margin: 14vh auto 0; }
.login .brand { justify-content: center; margin-bottom: 22px; }
.login .card { padding: 22px; }
.login button { width: 100%; justify-content: center; }
.login input { width: 100%; }

.foot { margin-top: 34px; }

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; }
}
"""

_MARK = (
    '<span class=mark aria-hidden=true>'
    '<svg width=22 height=22 viewBox="0 0 24 24" fill=none stroke="#fff" '
    'stroke-width=2.2 stroke-linecap=round>'
    '<circle cx=12 cy=12 r=6 opacity=.95/>'
    '<path d="M12 1.3v4.2M12 18.5v4.2M1.3 12h4.2M18.5 12h4.2"/>'
    '<circle cx=12 cy=12 r=1.7 fill="#fff" stroke=none/>'
    "</svg></span>"
)


# Was sich fürs Schnäppchenjagen wirklich lohnt: dieselbe Ware, anderer Markt.
# Alle 22 Länder anzubieten macht die Auswahl unübersichtlich — der Rest lässt
# sich weiterhin tippen.
_ANGEBOTENE_LAENDER = ("de", "fr", "nl", "it", "es", "be", "at", "pl", "uk")


def _laender_auswahl(feld_id: str, vorausgewaehlt: tuple[str, ...] = ()) -> str:
    """Ankreuzfelder für die gängigen Länderdomains.

    `vorausgewaehlt` sind Hosts aus `EXTRA_COUNTRIES` — was dort steht, gilt
    ohnehin für jede neue Suche, also ist es hier auch angekreuzt.
    """
    aktiv = {domains.normalize_host(h) for h in vorausgewaehlt}
    chips = []
    for kuerzel in _ANGEBOTENE_LAENDER:
        domain = domains.resolve(kuerzel)
        if domain is None:
            continue
        label = domain.host.removeprefix("www.vinted.")
        haken = " checked" if domain.host in aktiv else ""
        chips.append(
            f'<label class=chip><input type=checkbox name=laender '
            f'value="{escape(kuerzel)}" '
            f'id="{escape(feld_id)}-{escape(kuerzel)}"{haken}>'
            f"<span>{domain.flag} .{escape(label)}</span></label>"
        )
    zusatz = " — aus EXTRA_COUNTRIES vorausgewählt" if aktiv else ""
    return (
        "<fieldset class=laender>"
        f"<legend>Zusätzlich in diesen Ländern suchen{zusatz}</legend>"
        f"<div class=chips>{''.join(chips)}</div>"
        "</fieldset>"
    )


def _auswahl_leiste(watches: list[Watch]) -> str:
    """Werkzeugleiste für Aktionen auf mehreren Suchen.

    Das Formular selbst ist leer und steht neben der Liste: die Ankreuzfelder
    in den Karten verweisen mit `form=bulk` darauf. So bleiben die
    Einzelaktionen je Karte eigene Formulare — verschachtelte Formulare gibt es
    in HTML nicht.

    Ohne Javascript kreuzt man von Hand an und alles funktioniert; das kurze
    Skript unten fügt nur „alle" und „je Land" hinzu.
    """
    if not watches:
        return ""

    hosts = sorted({w.host for w in watches})
    laender = "".join(
        f'<button type=button class="ghost small" data-pick="{escape(host)}">'
        f"{domains.lookup(host).flag} {escape(host.removeprefix('www.vinted.'))}</button>"
        for host in hosts
    ) if len(hosts) > 1 else ""

    return f"""
    <form method=post action="/bulk" id=bulk class="card toolbar"
          onsubmit="return vsBestaetigen(event)">
      <div class=row>
        <button type=button class="ghost small" data-pick="*">alle</button>
        <button type=button class="ghost small" data-pick="">keine</button>
        {laender}
        <span class=spacer></span>
        <span class=chosen id=vs-anzahl>0 ausgewählt</span>
        <button class="ghost small" name=action value=pause>Pausieren</button>
        <button class="ghost small" name=action value=resume>Fortsetzen</button>
        <button class="danger small" name=action value=delete>Löschen</button>
      </div>
    </form>
    <script>
    (function () {{
      var form = document.getElementById('bulk');
      var zaehler = document.getElementById('vs-anzahl');
      function boxen() {{
        return Array.prototype.slice.call(
          document.querySelectorAll('input[name=ids]'));
      }}
      function zaehlen() {{
        var n = boxen().filter(function (b) {{ return b.checked; }}).length;
        zaehler.textContent = n + ' ausgewählt';
      }}
      document.addEventListener('click', function (e) {{
        var knopf = e.target.closest('[data-pick]');
        if (!knopf) return;
        var wahl = knopf.getAttribute('data-pick');
        boxen().forEach(function (b) {{
          b.checked = wahl === '*' || (wahl !== '' && b.dataset.host === wahl);
        }});
        zaehlen();
      }});
      document.addEventListener('change', function (e) {{
        if (e.target.name === 'ids') zaehlen();
      }});
      window.vsBestaetigen = function (e) {{
        var n = boxen().filter(function (b) {{ return b.checked; }}).length;
        if (!n) {{
          alert('Erst Suchen auswählen.');
          return false;
        }}
        if (e.submitter && e.submitter.value === 'delete') {{
          return confirm(n + ' Suche(n) endgültig löschen?');
        }}
        return true;
      }};
      zaehlen();
    }})();
    </script>
    """


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=de><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        '<meta name=robots content="noindex,nofollow">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><div class=wrap>{body}</div></body></html>"
    )


def error_page(exc: BaseException, path: str) -> str:
    """Absturzseite: was, wo, und wie man mehr erfährt."""
    import traceback

    stelle = ""
    frames = traceback.extract_tb(exc.__traceback__)
    if frames:
        letzte = frames[-1]
        stelle = f"{letzte.filename.rsplit('/', 1)[-1]}, Zeile {letzte.lineno}"
    return _page(
        "Fehler · Vinted Sniper",
        f"""
        <header class=top>
          <div class=brand>{_MARK}<div><h1>Vinted Sniper</h1><p>Da ist etwas schiefgegangen.</p></div></div>
        </header>
        <div class="flash bad"><span class=icon>⚠</span>
          <div><b>{escape(type(exc).__name__)}</b>: {escape(str(exc) or "ohne Meldung")}
          {f"<br><span class=meta>in {escape(stelle)}</span>" if stelle else ""}
          <br><span class=meta>bei {escape(path)}</span></div>
        </div>
        <div class=card>
          <div class=meta>Das vollständige Protokoll steht im Log:</div>
          <pre><code>docker compose logs --tail 80 sniper</code></pre>
          <div class=meta>Die Suchen und Alerts laufen davon unabhängig weiter.
          <a href="/">Zurück zur Übersicht</a></div>
        </div>
        """,
    )


def login_page(*, error: str | None = None) -> str:
    warn = (
        f'<div class="flash bad"><span class=icon>⚠</span><div>{escape(error)}</div></div>'
        if error
        else ""
    )
    return _page(
        "Anmelden · Vinted Sniper",
        f"""
        <div class=login>
          <div class=brand>
            {_MARK}
            <div>
              <h1>Vinted Sniper</h1>
              <p>Bitte anmelden.</p>
            </div>
          </div>
          {warn}
          <form method=post action="/login" class=card>
            <input type=password name=password placeholder="Passwort"
                   autofocus autocomplete="current-password" required
                   aria-label=Passwort>
            <div style="margin-top:10px">
              <button type=submit>Anmelden</button>
            </div>
          </form>
        </div>
        """,
    )


def _watch_card(watch: Watch, running: bool, paused: bool = False) -> str:
    domain = domains.lookup(watch.host)

    if not watch.enabled:
        pill = '<span class=pill>pausiert</span>'
    elif watch.last_error:
        pill = '<span class="pill err">Fehler</span>'
    elif running and paused:
        pill = '<span class=pill title="Außerhalb des Zeitfensters (ACTIVE_HOURS)">Nachtruhe</span>'
    elif running:
        pill = '<span class="pill on">läuft</span>'
    else:
        pill = '<span class=pill>startet</span>'

    ziel = (
        "Webhook"
        if watch.origin in ("file", "panel") or watch.webhook_url
        else f"Channel {watch.channel_id}"
    )
    fehler = (
        f"<div class=error>{escape(watch.last_error[:200])}</div>"
        if watch.last_error
        else ""
    )
    umschalten = "Fortsetzen" if not watch.enabled else "Pause"
    sep = '<span class=sep>·</span>'
    # Wie oft diese Suche fand, was eine andere schon gemeldet hatte. Bei
    # Länderkopien die Zahl, an der man sieht, ob sie sich lohnen.
    beitrag = (
        f'{sep}<span title="Funde, die eine andere Suche schon gemeldet hatte">'
        f"{watch.dupes} doppelt</span>"
        if watch.dupes
        else ""
    )

    return f"""
    <div class=card>
      <div class=watch>
        <label class=pick title="Suche #{watch.id} auswählen">
          <input type=checkbox name=ids value="{watch.id}" form=bulk
                 data-host="{escape(watch.host)}">
        </label>
        <div class=info>
          <div class=name><span class=id>#{watch.id}</span>{escape(watch.name)} {pill}</div>
          <div class=meta>{domain.flag} {escape(watch.host)}{sep}{escape(watch.query.describe())}</div>
          <div class=meta>alle {watch.interval}s{sep}{watch.hits} Treffer{beitrag}{sep}{ziel}{sep}<a
             href="{escape(watch.source_url)}" target=_blank rel=noopener>auf Vinted öffnen ↗</a></div>
          {fehler}
        </div>
        <div class=actions>
          <form method=post action="/watch/{watch.id}/interval">
            <input class=small type=number name=interval value="{watch.interval}"
                   min=20 step=10 aria-label="Intervall in Sekunden">
            <button class="ghost small" type=submit>Speichern</button>
          </form>
          <form method=post action="/watch/{watch.id}/toggle">
            <button class="ghost small" type=submit>{umschalten}</button>
          </form>
          <form method=post action="/watch/{watch.id}/delete"
                onsubmit="return confirm('Suche #{watch.id} wirklich löschen?')">
            <button class="danger small" type=submit>Löschen</button>
          </form>
        </div>
      </div>
    </div>
    """


def dashboard(
    *,
    watches: list[Watch],
    running: set[int],
    sessions: dict[str, str],
    started_at: dt.datetime,
    message: str | None = None,
    error: str | None = None,
    default_countries: tuple[str, ...] = (),
    traffic_line: str = "",
    betrieb: list[Row] | None = None,
    paused: bool = False,
) -> str:
    flash = ""
    if error:
        flash = (
            '<div class="flash bad"><span class=icon>⚠</span>'
            f"<div>{escape(error)}</div></div>"
        )
    elif message:
        flash = (
            '<div class=flash><span class=icon>✓</span>'
            f"<div>{escape(message)}</div></div>"
        )

    aktiv = sum(1 for w in watches if w.enabled)
    treffer = sum(w.hits for w in watches)
    laufzeit = dt.datetime.now(dt.timezone.utc) - started_at
    stunden = int(laufzeit.total_seconds() // 3600)
    laufzeit_text = (
        f"{stunden} h" if stunden else f"{int(laufzeit.total_seconds() // 60)} Min"
    )

    karten = "".join(_watch_card(w, w.id in running, paused) for w in watches) or (
        "<div class=card><div class=meta>Noch keine Suche angelegt. Oben eine "
        "Vinted-Adresse einfügen — die Filter kommen automatisch mit.</div></div>"
    )

    volumen = (
        f'<div class=meta style="margin-top:12px;padding-top:12px;'
        f'border-top:1px solid var(--line-soft)">📦 Proxy-Volumen: '
        f"{escape(traffic_line)}</div>"
        if traffic_line
        else ""
    )

    zustand = "".join(
        "<div class=session><span class=dot></span>"
        f"<b>{escape(host)}</b><span>{escape(state)}</span></div>"
        for host, state in sessions.items()
    ) or '<div class=meta>Noch keine Verbindung aufgebaut.</div>'

    # Was gilt, steht hier — nicht in der .env. Markiert ist, was man
    # wahrscheinlich nicht so wollte.
    bericht = (
        "<div class=card><h2 style='margin-top:0'>Betrieb</h2><div class=betrieb>"
        + "".join(
            f"<div class={'warn' if z.warn else 'ok'}><b>{escape(z.label)}</b></div>"
            f"<div class={'warn' if z.warn else 'ok'}><span>{escape(z.value)}</span></div>"
            for z in betrieb
        )
        + "</div></div>"
        if betrieb
        else ""
    )

    return _page(
        "Vinted Sniper",
        f"""
        <header class=top>
          <div class=brand>
            {_MARK}
            <div>
              <h1>Vinted Sniper</h1>
              <p>Suchen verwalten — Alerts kommen in Discord an.</p>
            </div>
          </div>
          <form method=post action="/logout">
            <button class="ghost small" type=submit>Abmelden</button>
          </form>
        </header>

        {flash}

        <div class=stats>
          <div class=stat><b>{aktiv}</b><span>aktiv</span></div>
          <div class=stat><b>{len(watches) - aktiv}</b><span>pausiert</span></div>
          <div class=stat><b>{treffer}</b><span>Treffer</span></div>
          <div class=stat><b>{laufzeit_text}</b><span>Laufzeit</span></div>
        </div>

        {bericht}

        <div class=card>
          <div class=sessions>{zustand}</div>
          {volumen}
        </div>

        <h2>Neue Suche</h2>
        <form method=post action="/add" class=card>
          <div class=row>
            <input class=grow type=url name=url required
                   placeholder="https://www.vinted.de/catalog?search_text=…"
                   aria-label="Such-URL von Vinted">
          </div>
          <div class=row style="margin-top:10px">
            <input name=name placeholder="Name (optional)" aria-label=Name>
            <input type=number name=interval placeholder="Sekunden" min=20 step=10
                   aria-label="Intervall in Sekunden">
            <button type=submit>Hinzufügen</button>
          </div>
          {_laender_auswahl("neu", default_countries)}
          <div class=hint>
            Suche auf Vinted zusammenklicken, Adresszeile kopieren, hier einfügen.
            Alle Filter kommen mit. Die Adresse wird sofort getestet.
            Angekreuzte Länder legen dieselbe Suche zusätzlich dort an — derselbe
            Artikel kostet in Frankreich oft weniger.
          </div>
        </form>

        <details class=bulk>
          <summary>Mehrere auf einmal <small>— eine Adresse je Zeile</small></summary>
          <div class=body>
            <form method=post action="/import">
              <textarea name=urls rows=8 required
                        aria-label="Such-URLs, eine je Zeile"
                        placeholder="https://www.vinted.de/catalog?search_text=nike+air+max&#10;https://www.vinted.de/catalog?search_text=carhartt&amp;price_to=40&#10;&#10;# Zeilen mit Raute werden übersprungen&#10;Stone Island FR | https://www.vinted.fr/catalog?search_text=stone+island"></textarea>
              <div class=row style="margin-top:10px">
                <input type=number name=interval placeholder="Sekunden" min=20 step=10
                       aria-label="Intervall in Sekunden">
                <button type=submit>Alle importieren</button>
              </div>
              {_laender_auswahl("bulk", default_countries)}
              <div class=hint>
                Leerzeilen, Kommentare mit <code>#</code>, doppelte und bereits
                angelegte Suchen werden übersprungen. Ein Name lässt sich mit
                <code>Name | Adresse</code> voranstellen. Hier wird nicht live
                geprüft — fünfzig Abfragen auf einen Schlag provozieren nur eine
                Sperre; untaugliche Suchen fallen nach dem ersten Durchlauf auf.
              </div>
            </form>
          </div>
        </details>

        <h2>Suchen <span class=count>({len(watches)})</span></h2>
        {_auswahl_leiste(watches)}
        {karten}
        """,
    )
