from __future__ import annotations

import threading

from flask import Response, request

from .. import system_integration as si
from ..network import firewall, qr_manager
from ..network.hotspot_manager import HotspotManager
from ..security import validation as v
from ..security.authentication import ADMIN, EVERYONE, WRITERS, current_role, require
from ..services import Invalid, NotFound
from ..services.backup_service import BackupError
from . import api, ctx, ok, parse


@api.get("/system/ping")
def ping():
    """Erreichbarkeitsprüfung (ohne Anmeldung, ohne Inhalte)."""
    return ok({"pong": True})


@api.get("/system/status")
@require(*EVERYONE)
def status():
    return ok(ctx().status(full=current_role() == ADMIN))


@api.get("/system/network")
@require(*EVERYONE)
def network():
    return ok(ctx().network_info(current_role()))


@api.post("/system/network/refresh")
@require(*WRITERS)
def network_refresh():
    c = ctx()
    c.network.refresh(try_hotspot=True)
    return ok(c.network_info(current_role()))


@api.get("/system/qr/<kind>.svg")
@require(*EVERYONE)
def qr(kind: str):
    info = ctx().network_info(current_role())
    data = {"controller": info.get("controller_url"), "viewer": info.get("viewer_url"),
            "wifi": info.get("wifi_qr")}.get(kind)
    if not data:
        raise NotFound("Dieser QR-Code ist nicht verfügbar.")
    return Response(qr_manager.svg(data), mimetype="image/svg+xml", headers={"Cache-Control": "no-store"})


@api.post("/system/hotspot/open-settings")
@require(ADMIN)
def hotspot_settings():
    if not HotspotManager.open_settings():
        raise Invalid("Die Windows-Einstellungen konnten nicht geöffnet werden.")
    return ok({"opened": True})


@api.post("/system/tokens/rotate")
@require(ADMIN)
def rotate_token():
    c = ctx()
    role = parse(v.TokenRotate).role
    c.secrets.rotate(role)
    c.publish_network()
    return ok(c.network_info(ADMIN))


@api.get("/system/settings")
@require(ADMIN)
def get_settings():
    return ok(ctx().settings_info())


@api.patch("/system/settings")
@require(ADMIN)
def patch_settings():
    c = ctx()
    data = parse(v.SettingsUpdate).model_dump(exclude_none=True)
    before = c.settings.value
    if "autostart" in data and data["autostart"] != si.autostart_enabled():
        if not si.set_autostart(data["autostart"]):
            raise Invalid("Autostart lässt sich nur in der installierten Flip7Live.exe einrichten.")
    c.settings.update(**data)
    if data.get("network_mode", before.network_mode) != before.network_mode:
        threading.Thread(target=c.network.refresh, kwargs={"try_hotspot": True}, daemon=True).start()
    return ok(c.settings_info())


@api.get("/system/backups")
@require(ADMIN)
def list_backups():
    return ok(ctx().backup.list())


@api.post("/system/backups")
@require(ADMIN)
def create_backup():
    try:
        return ok(ctx().backup.create("manual"), 201)
    except BackupError as exc:
        raise Invalid(str(exc)) from None


@api.post("/system/backups/restore")
@require(ADMIN)
def restore_backup():
    c = ctx()
    try:
        result = c.restore_backup(parse(v.BackupRestore).name)
    except BackupError as exc:
        raise Invalid(str(exc), code="backup_invalid") from None
    return ok(result)


@api.get("/system/firewall")
@require(ADMIN)
def firewall_status():
    return ok(firewall.check())


@api.post("/system/firewall/allow")
@require(ADMIN)
def firewall_allow():
    """Nach ausdrücklicher Bestätigung (Button + Windows-UAC): Regel nur für private Netzwerke anlegen und – mit
    ``make_private`` – das aktuell genutzte Netzwerk von „Öffentlich“ auf „Privat“ stellen (eine UAC-Abfrage)."""
    body = request.get_json(silent=True) or {}
    net = ctx().network
    primary = net.state.primary
    private_if = (primary.index if body.get("make_private") is True and primary and primary.category == "Public"
                  and net.state.mode != "hotspot" else None)
    port = net.port if si.frozen() else None  # Regel gilt dem Programm – nur für die Flip7Live.exe sinnvoll
    if port is None and private_if is None:
        raise Invalid("Die Firewall-Regel kann nur für die Flip7Live.exe angelegt werden.")
    if not firewall.request_setup(port, private_if):
        raise Invalid("Die Windows-Abfrage für Administratorrechte konnte nicht geöffnet werden.")
    return ok({"requested": True, "rule": port is not None, "make_private": private_if is not None})


@api.post("/system/reset")
@require(ADMIN)
def reset_all():
    """Nur am Laptop: alle Spieldaten löschen. Erfordert {"confirm": "LÖSCHEN"}; vorher automatische Sicherung."""
    body = request.get_json(silent=True) or {}
    if str(body.get("confirm", "")).strip().upper() != "LÖSCHEN":
        raise Invalid("Zur Bestätigung bitte LÖSCHEN eingeben.", code="confirm_required")
    c = ctx()
    backup = c.backup.create("prereset")
    counts = c.games.delete_everything()
    c.realtime.broadcast_reset("reset")
    return ok({**counts, "backup": backup["name"]})


@api.post("/system/open")
@require(ADMIN)
def open_path():
    target = (request.get_json(silent=True) or {}).get("target")
    if target == "network_settings":  # Windows: Netzwerkprofil (Öffentlich/Privat) ändern
        if not si.open_uri("ms-settings:network-status"):
            raise Invalid("Die Windows-Einstellungen konnten nicht geöffnet werden.")
        return ok({"opened": target})
    paths = ctx().paths
    path = {"log": paths.log_file, "data": paths.root, "backups": paths.backups}.get(target)
    if path is None:
        raise Invalid("Unbekanntes Ziel.")
    if not si.open_path(path):
        raise Invalid("Konnte nicht geöffnet werden.")
    return ok({"opened": str(path)})


@api.post("/system/shutdown")
@require(ADMIN)
def shutdown():
    ctx().request_shutdown()
    return ok({"shutting_down": True})
