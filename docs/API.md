# API

Basis: `http://<laptop-ip>:<port>/api/v1` · Format JSON · Zeiten in UTC (ISO 8601 mit `Z`).

## Antworten

```json
{ "ok": true,  "data": { … } }
{ "ok": false, "error": { "code": "missing_points", "message": "Bitte für mindestens einen Spieler Punkte eintragen (auch 0).", "details": { … } } }
```

| Status | Code (Beispiele) | Bedeutung |
|---|---|---|
| 400 | `bad_request` | ungültige Anfrage |
| 401 | `unauthenticated`, `expired`, `revoked`, `admin_remote` | keine/abgelaufene/widerrufene Sitzung, Admin nur lokal |
| 403 | `forbidden`, `csrf`, `origin` | Rolle reicht nicht, CSRF-Token fehlt, fremde Seite |
| 404 | `not_found` | nicht vorhanden |
| 409 | `open_game_exists`, `open_night_exists`, `round_mismatch`, `invalid_status`, `game_running`, `player_exists` | Konflikt mit dem aktuellen Zustand |
| 421 | `host_not_allowed` | unbekannter Host-Header (DNS-Rebinding) |
| 422 | `validation_error`, `missing_points`, `points_out_of_range`, `unsupported_media_type`, `backup_invalid` | Eingabe ungültig |
| 429 | `rate_limited` | zu viele Anfragen |
| 503 | `database_busy` | Datenbank vorübergehend gesperrt |

## Authentifizierung

| Rolle | Wie | Darf |
|---|---|---|
| `admin` | `/admin/login?once=…` (einmaliger Link, nur von 127.0.0.1) | alles |
| `controller` | `/controller?key=<Steuerungstoken>` (QR-Code) | lesen, Spiele/Abende/Runden/Spieler schreiben, Netzwerk neu prüfen, Export |
| `viewer` | `/dashboard?key=<Zuschauertoken>` (QR-Code) | lesen |

Danach gilt das Sitzungs-Cookie `flip7live_session` (HttpOnly, SameSite=Strict, 24 h). **Jede schreibende Anfrage**
(POST/PATCH/DELETE) braucht den Header `X-CSRF-Token` (steht im `<meta name="csrf-token">` der Seite) und
`Content-Type: application/json`.

## Spieleabende

| Methode | Pfad | Rolle | Körper / Hinweis |
|---|---|---|---|
| POST | `/nights` | writer | `{"name"?, "target_score"?, "player_ids": [..≥2]}` – startet Partie 1 → Spielstand (201) |
| GET | `/nights?order=asc\|desc` | alle | Liste mit Abendstand |
| GET | `/nights/{id}` | alle | Abendstand: `standings` (rank, wins, shared_wins, games_played, total_points, best_round), `games`, `leaders` |
| PATCH | `/nights/{id}` | writer | `{"name"?, "target_score"?}` – neue Punktzahl gilt ab der nächsten Partie |
| POST | `/nights/{id}/next-game` | writer | `{"player_ids"?, "target_score"?}` → Spielstand der neuen Partie (201) |
| POST | `/nights/{id}/finish` | writer | Abend beenden → Abendstand (409 `game_running`, solange eine Partie mit Runden läuft) |
| DELETE | `/nights/{id}` | admin | nur beendete Abende |

## Spiele (Partien)

| Methode | Pfad | Rolle | Körper / Hinweis |
|---|---|---|---|
| POST | `/games` | writer | `{"name"?, "target_score"?, "player_ids"}` – Einzelspiel (Status CREATED) |
| GET | `/games?status=FINISHED,CANCELLED&order=asc` | alle | Liste |
| GET | `/games/current` | alle | offenes Spiel oder `null`; `?display=1`: wie das Dashboard (auch zuletzt beendete Partie) |
| GET | `/games/{id}` | alle | vollständiger Spielstand (siehe unten) |
| PATCH | `/games/{id}` | writer | `{"name"?, "target_score"?, "player_ids"?}` – Ziel/Spieler nur vor dem Start |
| POST | `/games/{id}/start` | writer | CREATED → ACTIVE |
| POST | `/games/{id}/finish` | writer | manuelles Ende |
| POST | `/games/{id}/cancel` | writer | abbrechen (zählt nicht in der Statistik) |
| POST | `/games/{id}/continue` | writer | `{"target_score"?}` – nach Ende weiterspielen (ohne Ziel: nur manuelles Ende) |
| DELETE | `/games/{id}` | admin | nur beendete/abgebrochene |
| GET | `/games/{id}/events` | writer | Änderungsprotokoll (u. a. alte/neue Punkte bei Korrekturen) |

**Spielstand** (`GET /games/{id}`, auch in `game:updated`): `game` (id, status, target_score, current_round, rounds_played,
night_id, night_number, finish_reason …), `players`, `leaderboard` (rank, total, last_round_points, progress,
progress_bar ≤ 100, to_target, reached_target), `leaders`, `tie`, `winners`, `rounds` (scores je Spieler-ID),
`statistics` (je Spieler, Führungswechsel, Diagrammreihen), `night` (Abendstand oder `null`).

## Runden

| Methode | Pfad | Rolle | Körper |
|---|---|---|---|
| GET | `/games/{id}/rounds` | alle | |
| POST | `/games/{id}/rounds` | writer | `{"idempotency_key": "8–64 Zeichen [A-Za-z0-9_-]", "round_number": n, "scores": [{"player_id": 1, "points": 25}, …]}` → 201, Wiederholung mit gleichem Schlüssel → 200 `replayed: true` |
| PATCH | `/rounds/{id}` | writer | `{"idempotency_key"?, "scores": [...]}` |
| DELETE | `/rounds/{id}` | writer | nachfolgende Runden rücken auf |

`points` muss eine ganze Zahl zwischen 0 und dem eingestellten Höchstwert sein. `null` oder ein fehlender Spieler bedeutet „Runde nicht mitgespielt“: Es wird kein Punkteeintrag gespeichert (zählt weder als Runde noch als 0-Runde). Mindestens ein Spieler muss Punkte haben, sonst `422 missing_points`. Beim Korrigieren (`PATCH`) kann ein Spieler so zwischen „mitgespielt“ und „nicht mitgespielt“ wechseln.

## Spieler

| Methode | Pfad | Rolle | Körper |
|---|---|---|---|
| GET | `/players` | alle | inkl. `last_played_at` |
| POST | `/players` | writer | `{"display_name"}` – 409 `player_exists` mit vorhandenem Spieler in `details.player` |
| PATCH | `/players/{id}` | writer | `{"display_name"}` (gleiche ID, Statistik bleibt) |

## Statistik, Historie, Export

| Methode | Pfad | Rolle |
|---|---|---|
| GET | `/games/{id}/leaderboard` | alle |
| GET | `/games/{id}/statistics` | alle |
| GET | `/statistics/overall` | alle – Abende, Partien, Runden, je Spieler Siege/Quote/Durchschnitte/Abendsiege, `tie_rule` |
| GET | `/history?order=` | alle – beendete und abgebrochene Partien |
| GET | `/history/export.csv?game_id=1,2` | writer – Semikolon, UTF-8 mit BOM, eine Zeile je Spieler und Runde |
| GET | `/history/export.json?game_id=` | writer |

## System

| Methode | Pfad | Rolle | Hinweis |
|---|---|---|---|
| GET | `/system/ping` | – | Erreichbarkeit, ohne Inhalte |
| GET | `/system/status` | alle | Server, Port, Netzwerkmodus, Geräte, letzte Synchronisierung; Admin zusätzlich Datenbank, Geräteliste |
| GET | `/system/network` | alle | Admin: inkl. `controller_url`, Hotspot-Passwort; Steuerung: `viewer_url`, ggf. `wifi_qr` |
| POST | `/system/network/refresh` | writer | vollständig neu prüfen (versucht ggf. den Hotspot) |
| GET | `/system/qr/{controller\|viewer\|wifi}.svg` | je nach Rolle | QR-Code als SVG |
| POST | `/system/hotspot/open-settings` | admin | Windows-Einstellungen „Mobiler Hotspot“ |
| POST | `/system/tokens/rotate` | admin | `{"role": "controller"\|"viewer"}` |
| GET/PATCH | `/system/settings` | admin | Einstellungen (Autostart, Browser, Standardziel, Spieleranzahl, Modus, Port, Hotspot, Höchstpunkte, Sicherungen, `setup_completed`) |
| GET/POST | `/system/backups` | admin | Liste / manuelle Sicherung |
| POST | `/system/backups/restore` | admin | `{"name": "flip7live-….db"}` – geprüft, vorher Sicherheitskopie |
| GET | `/system/firewall` | admin | Zustand der Freigabe |
| POST | `/system/firewall/allow` | admin | `{"make_private"?: true}` – eine UAC-Abfrage: Regel nur für private Netzwerke und diesen Port (ersetzt vorhandene eingehende Regeln dieses Programms, auch Block-Regeln); mit `make_private` wird zusätzlich das aktuell genutzte Netzwerk von „Öffentlich“ auf „Privat“ gestellt (nicht im Hotspot-Modus). Antwort `{"requested", "rule", "make_private"}` |
| POST | `/system/open` | admin | `{"target": "log"\|"data"\|"backups"\|"network_settings"}` |
| POST | `/system/shutdown` | admin | Anwendung beenden |

Intern: `POST /internal/open` (nur 127.0.0.1, Header `X-Flip7-IPC` aus `config/instance.json`) – der Zweitstart der EXE holt
sich hier einen frischen Admin-Anmeldelink.

## Socket.IO

Verbindung zu `/socket.io/` mit dem Sitzungs-Cookie. Server → Client:

| Ereignis | Nutzdaten | Empfänger |
|---|---|---|
| `game:created`, `game:started` | `{game_id, game}` | alle |
| `game:finished` | `{game_id, game, winners, tie}` | alle |
| `game:updated` | `{game_id, state}` – vollständiger Spielstand (immer zuletzt) | alle |
| `round:created`, `round:updated`, `round:deleted` | `{game_id, round \| deleted_round_number}` | alle |
| `scores:updated` | `{game_id, rounds}` | alle |
| `leaderboard:updated` | `{game_id, leaderboard, leaders, tie}` | alle |
| `client:connected`, `client:disconnected` | `{clients: {total, admin, controller, viewer}}` | Admin |
| `network:changed` | Netzwerkinfo je Rolle (wie `/system/network`) | alle / Steuerung / Admin |
| `system:status` | bei Verbindung `{connected, role, server_version}`; `{reload: true}` nach Wiederherstellung | – |
| `error` | `{code, message}` z. B. `write_not_allowed` | Absender |

Client → Server: nur `state:request` (sendet `game:updated` mit dem aktuellen Stand zurück).
