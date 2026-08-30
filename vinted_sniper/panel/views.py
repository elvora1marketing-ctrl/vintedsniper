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
from ..vinted import domains

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9; --card: #fff; --text: #16181d; --muted: #6b7280;
  --line: #e3e6ea; --accent: #09b1ba; --danger: #d64545; --ok: #2e9e5b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a; --card: #1c1f25; --text: #e8eaed; --muted: #9aa1ab;
    --line: #2b2f37;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 16px 64px; background: var(--bg); color: var(--text);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 900px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 28px 0 10px; color: var(--muted);
     text-transform: uppercase; letter-spacing: .04em; }
.sub { color: var(--muted); margin: 0 0 24px; }
.card { background: var(--card); border: 1px solid var(--line);
        border-radius: 10px; padding: 16px; margin-bottom: 12px; }
.row { display: flex; gap: 12px; align-items: flex-start; flex-wrap: wrap; }
.grow { flex: 1 1 320px; min-width: 0; }
input, select, button, textarea {
  font: inherit; padding: 9px 11px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--card); color: var(--text);
}
textarea { resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo,
           monospace; font-size: 13px; line-height: 1.5; }
input:focus, select:focus, textarea:focus {
  outline: 2px solid var(--accent); outline-offset: 1px;
}
code { font-size: 12.5px; background: var(--bg); border: 1px solid var(--line);
       border-radius: 4px; padding: 1px 5px; }
button { cursor: pointer; border-color: transparent; background: var(--accent);
         color: #fff; font-weight: 600; }
button.ghost { background: transparent; border-color: var(--line);
               color: var(--text); font-weight: 500; }
button.danger { background: transparent; border-color: var(--line);
                color: var(--danger); font-weight: 500; }
.name { font-weight: 600; }
.meta { color: var(--muted); font-size: 13.5px; margin-top: 3px;
        overflow-wrap: anywhere; }
.pill { display: inline-block; font-size: 12.5px; padding: 2px 9px;
        border-radius: 99px; border: 1px solid var(--line); }
.pill.on { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 40%, var(--line)); }
.pill.off { color: var(--muted); }
.pill.err { color: var(--danger); border-color: color-mix(in srgb, var(--danger) 40%, var(--line)); }
.actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.actions form { display: inline; }
.actions input[type=number] { width: 84px; }
.flash { padding: 11px 14px; border-radius: 8px; margin-bottom: 16px;
         border: 1px solid var(--line); background: var(--card); }
.flash.bad { color: var(--danger);
             border-color: color-mix(in srgb, var(--danger) 40%, var(--line)); }
.stats { display: flex; gap: 20px; flex-wrap: wrap; }
.stat b { display: block; font-size: 20px; }
.stat span { color: var(--muted); font-size: 13px; }
a { color: var(--accent); }
.login { max-width: 340px; margin: 12vh auto 0; }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=de><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><div class=wrap>{body}</div></body></html>"
    )


def login_page(*, error: str | None = None) -> str:
    warn = f'<div class="flash bad">{escape(error)}</div>' if error else ""
    return _page(
        "Anmelden · Vinted Sniper",
        f"""
        <div class=login>
          <h1>Vinted Sniper</h1>
          <p class=sub>Bitte anmelden.</p>
          {warn}
          <form method=post action="/login" class=card>
            <div class=row>
              <input class=grow type=password name=password placeholder="Passwort"
                     autofocus autocomplete="current-password" required>
            </div>
            <div class=row style="margin-top:10px">
              <button type=submit>Anmelden</button>
            </div>
          </form>
        </div>
        """,
    )


def _watch_card(watch: Watch, running: bool) -> str:
    domain = domains.lookup(watch.host)

    if not watch.enabled:
        pill = '<span class="pill off">pausiert</span>'
    elif watch.last_error:
        pill = '<span class="pill err">Fehler</span>'
    elif running:
        pill = '<span class="pill on">läuft</span>'
    else:
        pill = '<span class="pill off">startet</span>'

    ziel = (
        "Webhook"
        if watch.origin == "file" or watch.webhook_url
        else f"Channel {watch.channel_id}"
    )
    fehler = (
        f'<div class=meta style="color:var(--danger)">{escape(watch.last_error[:160])}</div>'
        if watch.last_error
        else ""
    )
    umschalten = "Fortsetzen" if not watch.enabled else "Pause"

    return f"""
    <div class=card>
      <div class=row>
        <div class=grow>
          <div class=name>#{watch.id} · {escape(watch.name)} {pill}</div>
          <div class=meta>{domain.flag} {escape(watch.host)} · {escape(watch.query.describe())}</div>
          <div class=meta>alle {watch.interval}s · {watch.hits} Treffer · {ziel}
            · <a href="{escape(watch.source_url)}" target=_blank rel=noopener>auf Vinted öffnen</a></div>
          {fehler}
        </div>
        <div class=actions>
          <form method=post action="/watch/{watch.id}/interval">
            <input type=number name=interval value="{watch.interval}" min=20 step=10
                   aria-label="Intervall in Sekunden">
            <button class=ghost type=submit>Speichern</button>
          </form>
          <form method=post action="/watch/{watch.id}/toggle">
            <button class=ghost type=submit>{umschalten}</button>
          </form>
          <form method=post action="/watch/{watch.id}/delete"
                onsubmit="return confirm('Suche #{watch.id} wirklich löschen?')">
            <button class=danger type=submit>Löschen</button>
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
) -> str:
    flash = ""
    if error:
        flash = f'<div class="flash bad">{escape(error)}</div>'
    elif message:
        flash = f'<div class=flash>{escape(message)}</div>'

    aktiv = sum(1 for w in watches if w.enabled)
    treffer = sum(w.hits for w in watches)
    laufzeit = dt.datetime.now(dt.timezone.utc) - started_at
    stunden = int(laufzeit.total_seconds() // 3600)
    laufzeit_text = f"{stunden} h" if stunden else f"{int(laufzeit.total_seconds() // 60)} Min"

    karten = (
        "".join(_watch_card(w, w.id in running) for w in watches)
        or '<div class=card><div class=meta>Noch keine Suche angelegt.</div></div>'
    )

    zustand = (
        "".join(
            f"<div class=meta><b>{escape(host)}</b> — {escape(state)}</div>"
            for host, state in sessions.items()
        )
        or '<div class=meta>Noch keine Verbindung aufgebaut.</div>'
    )

    return _page(
        "Vinted Sniper",
        f"""
        <h1>Vinted Sniper</h1>
        <p class=sub>Suchen verwalten. Alerts kommen weiterhin in Discord an.</p>
        {flash}

        <div class=card>
          <div class=stats>
            <div class=stat><b>{aktiv}</b><span>aktive Suchen</span></div>
            <div class=stat><b>{len(watches) - aktiv}</b><span>pausiert</span></div>
            <div class=stat><b>{treffer}</b><span>Treffer gesamt</span></div>
            <div class=stat><b>{laufzeit_text}</b><span>Laufzeit</span></div>
          </div>
          <div style="margin-top:14px">{zustand}</div>
        </div>

        <h2>Neue Suche</h2>
        <form method=post action="/add" class=card>
          <div class=row>
            <input class=grow type=url name=url required
                   placeholder="https://www.vinted.de/catalog?search_text=…"
                   aria-label="Such-URL von Vinted">
          </div>
          <div class="row" style="margin-top:10px">
            <input name=name placeholder="Name (optional)" aria-label=Name>
            <input type=number name=interval placeholder="Sekunden" min=20 step=10
                   aria-label="Intervall in Sekunden">
            <button type=submit>Hinzufügen</button>
          </div>
          <div class=meta style="margin-top:10px">
            Suche auf Vinted zusammenklicken, Adresszeile kopieren, hier einfügen.
            Alle Filter kommen automatisch mit.
          </div>
        </form>

        <h2>Mehrere auf einmal</h2>
        <form method=post action="/import" class=card>
          <div class=row>
            <textarea class=grow name=urls rows=7 required
                      aria-label="Such-URLs, eine je Zeile"
                      placeholder="https://www.vinted.de/catalog?search_text=nike+air+max&#10;https://www.vinted.de/catalog?search_text=carhartt&amp;price_to=40&#10;Stone Island | https://www.vinted.fr/catalog?search_text=stone+island"></textarea>
          </div>
          <div class="row" style="margin-top:10px">
            <input type=number name=interval placeholder="Sekunden" min=20 step=10
                   aria-label="Intervall in Sekunden">
            <button type=submit>Alle importieren</button>
          </div>
          <div class=meta style="margin-top:10px">
            Eine Adresse je Zeile. Leerzeilen und Zeilen mit <code>#</code> werden
            übersprungen, bereits vorhandene Suchen ebenso. Ein Name lässt sich
            mit <code>Name | Adresse</code> voranstellen.
          </div>
        </form>

        <h2>Suchen ({len(watches)})</h2>
        {karten}

        <form method=post action="/logout" style="margin-top:28px">
          <button class=ghost type=submit>Abmelden</button>
        </form>
        """,
    )
