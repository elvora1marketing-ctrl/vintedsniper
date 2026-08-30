"""Web-Panel zum Verwalten der Suchen.

Läuft im selben Prozess wie der Sniper und greift auf dieselbe Datenbank und
denselben Monitor zu. Änderungen wirken deshalb sofort — ohne Neustart, ohne
Umweg über eine Datei.

Alerts gehen weiterhin ausschließlich nach Discord; das Panel zeigt nur an und
verwaltet.

`PanelServer` wird bewusst nicht hier importiert: das Modul zieht aiohttp nach
sich, und die Anmeldelogik in `auth` soll ohne Webserver prüfbar bleiben.
Aufrufer nehmen `from .panel.app import PanelServer`.
"""
