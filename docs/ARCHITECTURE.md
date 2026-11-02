# Architektur

## Überblick

```
┌────────────── Laptop (Flip7Live.exe) ──────────────┐        ┌──── Smartphone ────┐
│ launcher.py   Einzelinstanz, Start, Infobereich     │        │ Browser            │
│ Flask-App     Seiten + REST-API /api/v1             │◄──────►│ /controller        │
│ Socket.IO     Live-Ereignisse (threading + WS)      │  WLAN  │ (Steuerungs-QR)    │
│ Services      Spiellogik, Statistik, Backup         │        └────────────────────┘
│ SQLite (WAL)  %LOCALAPPDATA%\Flip7Live\data         │        ┌──── Zuschauer ─────┐
│ Netzwerk      Adapter, WLAN, Hotspot, QR            │◄──────►│ /dashboard         │
└─────────────────────────────────────────────────────┘        └────────────────────┘
        ▲ Browser am Laptop (Admin, nur 127.0.0.1)
```

Der Server ist immer die einzige Wahrheit. Jede Änderung wird in einer Datenbanktransaktion gespeichert, danach berechnet
der Server den kompletten Spielstand neu und verteilt ihn an alle Geräte.

## Module

| Pfad | Aufgabe |
|---|---|
| `launcher.py` | Startablauf, Einzelinstanz, Zweitstart-Übergabe, Browser, Infobereich, sauberes Beenden |
| `app/config.py` | Pfade, `settings.json`, `secrets.json` (defekte Dateien werden beiseitegelegt) |
| `app/extensions.py` | `Database` (SQLite, WAL, Schreibsperre), Socket.IO-Instanz |
| `app/server.py` | `Flip7Context` (alle Dienste einer Instanz), App-Fabrik, Seiten, Schutzprüfungen, Webserver |
| `app/models/` | SQLAlchemy-Modelle |
| `app/services/` | Fachlogik ohne Flask: `game_service`, `night_service`, `scoring_service`, `statistics_service`, `history_service`, `backup_service`, `realtime_service` |
| `app/api/` | REST-Endpunkte (dünn: prüfen, Service aufrufen, Ereignisse verteilen) |
| `app/realtime/events.py` | Socket.IO-Handler (nur lesend) |
| `app/security/` | Rollen/Sitzungen/CSRF/Host-Prüfung, Tokens, Eingabevalidierung (Pydantic) |
| `app/network/` | Adaptererkennung, WLAN, Hotspot, Firewall, QR-Codes, Netzwerkmanager |
| `app/database/` | Alembic-Migrationen, beim Start automatisch |
| `app/templates/`, `app/static/` | Oberfläche (Design „Kartenkante“) |

## Datenfluss einer Runde

1. Smartphone `POST /api/v1/games/{id}/rounds` mit Idempotenzschlüssel und erwarteter Rundennummer.
2. Prüfung: Sitzung, CSRF-Token, Herkunft, Rate-Limit, Pydantic (nur ganze Zahlen ≥ 0, keine leeren Felder).
3. `GameService.create_round` – eine Transaktion: Runde + Punkte, Ereignisprotokoll, Spielende prüfen, Idempotenz-Eintrag.
4. `RealtimeService.broadcast` – `round:created`, `scores:updated`, `leaderboard:updated`, ggf. `game:finished`, zuletzt
   `game:updated` mit dem vollständigen Stand an alle verbundenen Geräte.
5. Regelmäßige Sicherung im Hintergrund (höchstens alle 10 Minuten, bei Spielende immer).

Ein erneut gesendeter Request mit demselben Schlüssel liefert die gespeicherte Antwort statt einer zweiten Runde. Speichern
zwei Geräte gleichzeitig dieselbe Runde, schlägt das zweite mit `409 round_mismatch` fehl (eindeutige Rundennummer).

## Spieleabend

Ein Spieleabend (`game_nights`) hat eine Punktzahl, die für jede seiner Partien gilt. Eine Partie (`games`) endet
automatisch, sobald nach einer vollständig gespeicherten Runde jemand die Punktzahl erreicht. Danach startet
`POST /nights/{id}/next-game` die nächste Partie mit denselben Spielern. Der Abend-Endstand zählt Partiesiege; gleich
viele Siege bedeuten Gleichstand (die Gesamtpunkte entscheiden nicht).

## Live-Kommunikation

- Flask-SocketIO im Modus `threading`, WebSocket über `simple-websocket` (Polling als Rückfallebene).
- Verbindungen nur mit gültiger Sitzung (Cookie) und erlaubtem Host; Origin-Prüfung durch Engine.IO (nur gleiche Herkunft).
- Räume: `all` (alle), `writers` (Admin + Steuerung), `admin` (nur Laptop – z. B. Netzwerkdaten mit Steuerungs-URL).
- Über den Socket wird nicht geschrieben; jedes andere Client-Ereignis außer `state:request` wird mit `error` beantwortet.
- Clients verbinden sich bei Abbruch selbst neu und fordern mit `state:request` den aktuellen Stand an. Am Smartphone gibt
  es höchstens **einen** unbestätigten Speichervorgang (keine Warteschlange); er behält seinen Idempotenzschlüssel und wird
  deutlich als „Nicht bestätigt“ angezeigt.

## Netzwerkmanager

- Erkennung über PowerShell (`Get-NetAdapter`, `Get-NetIPAddress`, `Get-NetConnectionProfile`), SSID über
  `netsh wlan show interfaces` (Rückfall: Profilname). Ohne PowerShell: Adressen über den Rechnernamen.
- Ignoriert: Loopback, nicht verbundene Adapter, 169.254.x.x, „Tentative“-Adressen, VPN/Tunnel/VM-Adapter.
- Moduswahl „Automatisch“: WLAN → LAN → bereits aktiver Hotspot → Hotspot-Start versuchen → Einzelgerätebetrieb mit
  Hinweisen. „Nur bestehendes WLAN“ startet nie einen Hotspot, „Hotspot bevorzugen“ zuerst den Hotspot.
- Der Server lauscht auf allen Schnittstellen; der Manager bestimmt nur die Adresse im QR-Code. Eine Hintergrundprüfung
  (alle 3 s günstig, alle 60 s vollständig) erkennt Netzwerkwechsel und verteilt `network:changed`.
- Netzwerk „Öffentlich“ → Warnung (Windows-Firewall blockiert dort meist eingehende Verbindungen).

### Hotspot – Einschränkungen

Gesteuert wird der **mobile Hotspot** über die WinRT-API `NetworkOperatorTetheringManager` (aus Windows PowerShell 5.1).
Das veraltete „Hosted Network“ wird nicht verwendet.

- Windows teilt beim mobilen Hotspot immer eine bestehende Verbindung. Ohne jede Verbindung lässt er sich auf vielen
  Geräten nicht starten – Flip 7 Live meldet das ehrlich und empfiehlt den Smartphone-Hotspot.
- Gruppenrichtlinie, Treiber oder Hardware können ihn sperren (Fähigkeit ≠ `Enabled`).
- Erfolg gilt erst, wenn Windows den Zustand `On` meldet; erst dann erscheint eine Adresse. Beim Start werden Name und
  Passwort des Windows-Hotspots auf die Werte aus den Einstellungen gesetzt.
- Der Hotspot-Adapter heißt je nach Treiber „Wi-Fi Direct Virtual Adapter“ oder ist – gemessen auf Intel Wi-Fi 7 – ein
  zweiter, gleich benannter WLAN-Adapter („WLAN 2“). Bei aktivem Hotspot gilt deshalb jeder WLAN-Adapter ohne
  Netzwerkprofil als Hotspot-Adapter (das verbundene WLAN hat immer ein Profil).
- Einen Hotspot, den der Benutzer selbst gestartet hat, beendet Flip 7 Live beim Schließen nicht.
- Manuelle Aktivierung: Schaltfläche „Windows-Hotspot-Einstellungen“ (`ms-settings:network-mobilehotspot`).

## Sicherheit

- **Admin**: nur über einen einmaligen, 2 Minuten gültigen Anmeldelink, den die App selbst öffnet, und nur solange die
  Anfrage von 127.0.0.1/::1 kommt (Socket-Adresse, kein Header). Ein kopiertes Admin-Cookie nützt im WLAN nichts.
- **Steuerung/Zuschauer**: zufällige Tokens (24 Byte) im QR-Code, Austausch gegen eine Sitzung; der Token verschwindet
  per Weiterleitung aus der Adresszeile. Erneuern macht alte Sitzungen ungültig (Generationszähler). Sitzungen laufen nach
  24 h ab.
- Schreibzugriffe: Rolle + CSRF-Token (Header) + Origin/`Sec-Fetch-Site` + nur JSON. Host-Header-Prüfung gegen
  DNS-Rebinding. Rate-Limits (allgemein, Schreiben, Token-Prüfung). CSP `default-src 'self'` ohne Inline-Skripte.
- Tokens werden in Protokollen geschwärzt (`key=***`); Werkzeugs eigenes Zugriffsprotokoll ist abgeschaltet.

## Frontend

Kein Build-Schritt: ES-Module unter `app/static/js/`. `core/` (API, Live-Verbindung, DOM, Einstellungen),
`components/` (Rangliste, Diagramm, Verbindung, Gewinner, Spieleabend, Toast, Modal …), `views/` (je Seite).
Gestaltung ausschließlich über `css/tokens.css` (Farben, Schriften, Abstände), `base.css`, `components.css` und
`css/views/*.css` – das Design ist austauschbar, ohne Logik oder API zu ändern.
