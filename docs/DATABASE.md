# Datenbank

SQLite unter `%LOCALAPPDATA%\Flip7Live\data\flip7live.db`, Zugriff über SQLAlchemy 2 (nur parametrisierte Abfragen).

Verbindungseinstellungen: `foreign_keys=ON`, `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=10000`.
Alle Schreibvorgänge laufen über `Database.write()`: eine Transaktion, serialisiert durch eine Schreibsperre der Anwendung
(die auch Wiederherstellung und Beenden respektieren). Bleibt die Datei trotz Wartezeit gesperrt → `503 database_busy`.

## Tabellen

```
game_nights 1 ──< games 1 ──< game_players >── 1 players
                    │
                    ├──< rounds 1 ──< round_scores >── 1 players
                    └──< game_events
idempotency_requests   alembic_version (Schemastand)
```

| Tabelle | Spalten | Regeln |
|---|---|---|
| `game_nights` | id, name, target_score, status (ACTIVE/FINISHED), created_at, finished_at | höchstens ein ACTIVE (Teilindex) |
| `games` | id, name, target_score, status (CREATED/ACTIVE/FINISHED/CANCELLED), current_round, auto_finish, finish_reason (target/manual), created_at, started_at, finished_at, night_id, night_number | höchstens ein ACTIVE (Teilindex); `night_id` → CASCADE |
| `players` | id, display_name, name_key, created_at | `name_key` (bereinigt, ohne Groß/Klein) eindeutig – Beziehungen laufen nie über den Namen |
| `game_players` | id, game_id, player_id, display_order | eindeutig (game_id, player_id) |
| `rounds` | id, game_id, round_number, created_at, updated_at | eindeutig (game_id, round_number), round_number > 0 |
| `round_scores` | id, round_id, player_id, points | eindeutig (round_id, player_id), points ≥ 0 |
| `game_events` | id, game_id, event_type, payload (JSON), actor, created_at | Protokoll aller Änderungen inkl. alter/neuer Werte bei Korrekturen |
| `idempotency_requests` | key, scope, status_code, response, created_at | bereits verarbeitete Schreibanfragen; nach 7 Tagen gelöscht |

`current_round` ist die Nummer der als Nächstes einzugebenden Runde. **Gesamtpunkte werden nie gespeichert**, sondern
immer aus `round_scores` berechnet. Nach dem Löschen einer Runde rücken die folgenden auf (lückenlose Nummern).

## Indizes

`ix_games_status`, `ix_games_created_at`, `ix_games_night_id`, `uq_games_single_active` (Teilindex),
`ix_game_nights_created_at`, `uq_game_nights_single_active` (Teilindex), `ix_game_players_player_id`,
`ix_round_scores_player_id`, `ix_game_events_game_id`, `ix_idempotency_requests_created_at` sowie die Indizes der
Eindeutigkeitsregeln.

## Migrationen

Alembic unter `app/database/migrations` (eingebettet in die EXE). Beim Start prüft `database.upgrade()` den Stand:

1. Datenbank aktuell → nichts zu tun.
2. Stammt sie von einer **neueren** Version → Start mit verständlicher Meldung abgebrochen (keine Änderung).
3. Sonst: **Sicherung** (`…-migration.db`), dann Migration. Schlägt sie fehl, wird die Sicherung wiederhergestellt und eine
   Meldung angezeigt.

| Revision | Inhalt |
|---|---|
| 0001 | Ausgangsschema |
| 0002 | Spieleabende (`game_nights`, `games.night_id`, `games.night_number`) |

Ein Test (`test_migrations_match_models`) stellt sicher, dass die Migrationen exakt den Modellen entsprechen.

Neue Migration (Entwicklung): Modelle ändern, dann mit Alembic `revision --autogenerate` eine Datei in
`app/database/migrations/versions/` erzeugen, prüfen, Test ausführen.

## Backup

- Methode: **SQLite-Online-Backup-API** (`sqlite3.Connection.backup`) – konsistent auch im WAL-Modus; die Datei wird nie
  einfach kopiert.
- Automatisch: vor Migrationen, bei Spieländerungen (höchstens alle 10 Minuten), bei jedem Spielende, vor einer
  Wiederherstellung. Manuell über Einstellungen › Daten & Backup.
- Aufbewahrung: die neuesten *n* (Einstellung „Anzahl aufbewahrter Sicherungen“, Standard 20) unter `backups\`.
- Prüfung vor der Wiederherstellung: `PRAGMA integrity_check`, erforderliche Tabellen, bekannter Schemastand.
- Wiederherstellung unter der Schreibsperre: Sicherheitskopie des aktuellen Stands, Backup-API in die aktive Datenbank,
  Verbindungen verwerfen, ggf. auf den aktuellen Schemastand migrieren, alle Geräte laden neu.
- Externe Sicherungsdatei wiederherstellen: in den Ordner `backups\` legen, dann erscheint sie in der Liste.
