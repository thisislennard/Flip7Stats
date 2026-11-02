"""Netzwerkmanager mit simulierten Windows-Antworten – es läuft nie PowerShell, es startet nie ein echter Hotspot."""

from conftest import LAN_IP, FakeNet

from app.network import address_resolver, qr_manager, wifi_detector
from app.network.address_resolver import Interface
from app.network.hotspot_manager import HotspotManager
from app.network.network_manager import NetworkManager

# Echte Antwortstruktur von Get-NetAdapter/Get-NetIPAddress (gekürzt, Werte anonymisiert)
SCAN = {
    "adapters": [
        {"ifIndex": 17, "Name": "WLAN", "InterfaceDescription": "Intel(R) Wi-Fi 7", "Status": "Up",
         "MediaType": "Native 802.11", "PhysicalMediaType": "Native 802.11", "Virtual": False, "HardwareInterface": True},
        {"ifIndex": 22, "Name": "Ethernet", "InterfaceDescription": "Intel(R) Ethernet", "Status": "Disconnected",
         "MediaType": "802.3", "PhysicalMediaType": "802.3", "Virtual": False, "HardwareInterface": True},
        {"ifIndex": 18, "Name": "LAN-Verbindung 2", "InterfaceDescription": "TAP-Windows Adapter V9 for OpenVPN Connect",
         "Status": "Up", "MediaType": "802.3", "PhysicalMediaType": "Unspecified", "Virtual": True, "HardwareInterface": False},
        {"ifIndex": 40, "Name": "vEthernet (WSL)", "InterfaceDescription": "Hyper-V Virtual Ethernet Adapter", "Status": "Up",
         "MediaType": "802.3", "PhysicalMediaType": "Unspecified", "Virtual": True, "HardwareInterface": False},
        {"ifIndex": 33, "Name": "Bluetooth-Netzwerkverbindung", "InterfaceDescription": "Bluetooth Device (PAN)",
         "Status": "Up", "MediaType": "802.3", "PhysicalMediaType": "BlueTooth", "Virtual": True, "HardwareInterface": False},
    ],
    "addresses": [
        {"InterfaceIndex": 17, "InterfaceAlias": "WLAN", "IPAddress": "192.168.178.50", "PrefixLength": 24, "AddressState": "Preferred"},
        {"InterfaceIndex": 22, "InterfaceAlias": "Ethernet", "IPAddress": "169.254.83.52", "PrefixLength": 16, "AddressState": "Tentative"},
        {"InterfaceIndex": 18, "InterfaceAlias": "LAN-Verbindung 2", "IPAddress": "10.8.0.2", "PrefixLength": 24, "AddressState": "Preferred"},
        {"InterfaceIndex": 40, "InterfaceAlias": "vEthernet (WSL)", "IPAddress": "172.20.0.1", "PrefixLength": 20, "AddressState": "Preferred"},
        {"InterfaceIndex": 33, "InterfaceAlias": "Bluetooth", "IPAddress": "169.254.1.2", "PrefixLength": 16, "AddressState": "Preferred"},
        {"InterfaceIndex": 1, "InterfaceAlias": "Loopback Pseudo-Interface 1", "IPAddress": "127.0.0.1", "PrefixLength": 8, "AddressState": "Preferred"},
    ],
    "profiles": [{"InterfaceIndex": 17, "Name": "Heimnetz", "NetworkCategory": "Public", "IPv4Connectivity": "Internet"}],
}


def test_scan_picks_wlan_and_ignores_virtual_loopback_and_apipa():
    result = address_resolver.parse_scan(SCAN)
    assert [(i.kind, i.ip) for i in result] == [("wifi", "192.168.178.50")]
    assert result[0].category == "Public" and result[0].subnet == "192.168.178.0/24"


def test_hotspot_adapter_is_recognised():
    data = {"adapters": [{"ifIndex": 9, "Name": "LAN-Verbindung* 10", "InterfaceDescription": "Microsoft Wi-Fi Direct Virtual Adapter #2",
                          "Status": "Up", "MediaType": "Native 802.11", "Virtual": True, "HardwareInterface": False}],
            "addresses": [{"InterfaceIndex": 9, "IPAddress": "192.168.137.1", "PrefixLength": 24, "AddressState": "Preferred"}]}
    assert address_resolver.parse_scan(data)[0].kind == "hotspot"


def test_netsh_parsing_german_and_english():
    de = "Es ist 1 Schnittstelle auf dem System vorhanden:\n\n    Name                   : WLAN\n    Status                  : Verbunden\n    SSID : Heimnetz\n    BSSID : aa:bb\n"
    en = "There is 1 interface on the system:\n\n    Name                   : Wi-Fi\n    State                  : connected\n    SSID                   : Home\n    BSSID                  : aa:bb\n"
    off = "    Name                   : WLAN\n    Status                  : Getrennt\n"
    assert wifi_detector.parse_netsh(de) == [wifi_detector.WifiInfo("WLAN", True, "Heimnetz")]
    assert wifi_detector.parse_netsh(en) == [wifi_detector.WifiInfo("Wi-Fi", True, "Home")]
    assert wifi_detector.parse_netsh(off)[0].connected is False


def manager(net: FakeNet, mode="auto"):
    settings = type("S", (), {"network_mode": mode, "hotspot_ssid": "Flip7-Live", "hotspot_password": "geheim123"})()
    m = NetworkManager(lambda: settings, hotspot=HotspotManager(runner=net.run_hotspot), scanner=net.scan,
                       wifi=lambda: list(net.wifi), fingerprint=lambda: ())
    m.check_reachable = lambda state=None: True
    m.port = 5000
    return m


def test_existing_wlan_is_used_without_hotspot():
    net = FakeNet()
    state = manager(net).refresh()
    assert state.mode == "wlan" and state.primary.ip == "192.168.178.50" and state.ssid == "Heimnetz"
    assert "start" not in net.hotspot_calls


def test_no_wlan_starts_hotspot_and_verifies_state():
    net = FakeNet()
    net.interfaces, net.wifi = [], []
    state = manager(net).refresh()
    assert net.hotspot_calls.count("start") == 1
    assert state.mode == "hotspot" and state.primary.ip == "192.168.137.1"
    assert state.hotspot.active and state.hotspot.started_by_app


def test_hotspot_not_supported_falls_back_to_local_with_hints():
    net = FakeNet()
    net.interfaces, net.wifi = [], []
    net.hotspot["capability"] = "DisabledByHardwareLimitation"
    state = manager(net).refresh()
    assert state.mode == "local" and state.primary is None
    assert any("Smartphone" in h for h in state.hints)
    assert any("unterstützt keinen Hotspot" in w for w in state.warnings)


def test_hotspot_refused_by_windows_is_not_reported_as_success():
    net = FakeNet()
    net.interfaces, net.wifi = [], []
    net.start_result = {"result": "OperationInProgress", "state": "Off", "message": "Kein Netz zum Teilen"}
    state = manager(net).refresh()
    assert state.mode == "local" and not state.hotspot.active
    assert "nicht gestartet" in state.hotspot.message


def test_hotspot_adapter_without_profile_is_recognised_when_active():
    """Gemessen auf Intel Wi-Fi 7: Der Hotspot erscheint als zweiter WLAN-Adapter „WLAN 2“ ohne Netzwerkprofil."""
    net = FakeNet()
    net.hotspot["state"] = "On"
    net.interfaces.append(Interface(10, "WLAN 2", "Intel(R) Wi-Fi 7 BE211", "wifi", "192.168.137.1", 24))
    state = manager(net, mode="prefer_hotspot").refresh()
    assert state.mode == "hotspot" and state.primary.name == "WLAN 2"
    assert [i.kind for i in state.interfaces] == ["wifi", "hotspot"]
    state = manager(net, mode="auto").refresh()
    assert state.mode == "wlan" and state.primary.name == "WLAN"


def test_wlan_only_never_starts_hotspot():
    net = FakeNet()
    net.interfaces, net.wifi = [], []
    state = manager(net, mode="wlan_only").refresh()
    assert state.mode == "local" and "start" not in net.hotspot_calls


def test_prefer_hotspot_mode():
    net = FakeNet()
    state = manager(net, mode="prefer_hotspot").refresh()
    assert state.mode == "hotspot"


def test_network_change_is_detected_and_published():
    net = FakeNet()
    m = manager(net)
    seen = []
    m.on_change(seen.append)
    m.refresh(try_hotspot=False)
    net.interfaces = [Interface(5, "WLAN", "Intel", "wifi", "172.20.10.3", 28, "iPhone", "Private")]  # Smartphone-Hotspot
    m.refresh(try_hotspot=False)
    assert [s.primary.ip for s in seen] == ["192.168.178.50", "172.20.10.3"]
    m.refresh(try_hotspot=False)
    assert len(seen) == 2  # unverändert → kein erneutes Ereignis


def test_public_network_warns_about_firewall():
    net = FakeNet()
    net.interfaces = [Interface(17, "WLAN", "Intel", "wifi", "192.168.178.50", 24, "Café", "Public")]
    state = manager(net).refresh()
    assert any("Öffentlich" in w for w in state.warnings)


def test_hotspot_is_only_stopped_if_app_started_it():
    net = FakeNet()
    net.hotspot["state"] = "On"  # vom Benutzer selbst gestartet
    m = manager(net, mode="prefer_hotspot")
    m.refresh()
    m.stop()
    assert "stop" not in net.hotspot_calls


def test_wifi_qr_payload_escapes_special_characters():
    assert qr_manager.wifi_payload("Flip7;Live", 'a"b') == 'WIFI:T:WPA;S:Flip7\\;Live;P:a\\"b;;'
    assert qr_manager.svg("http://x").startswith("<?xml") or "<svg" in qr_manager.svg("http://x")


def test_network_api_hides_secrets_by_role(admin, controller, viewer):
    full = admin.get("/api/v1/system/network").json["data"]
    assert "controller_url" in full and "key=" in full["controller_url"] and "hotspot_password" in full
    ctrl = controller.get("/api/v1/system/network").json["data"]
    assert "controller_url" not in ctrl and "viewer_url" in ctrl and "hotspot_password" not in ctrl
    view = viewer.get("/api/v1/system/network").json["data"]
    assert "viewer_url" not in view and "controller_url" not in view


def test_qr_codes_by_role(admin, controller, viewer):
    assert admin.get("/api/v1/system/qr/controller.svg").status_code == 200
    assert controller.get("/api/v1/system/qr/controller.svg").status_code == 404
    assert controller.get("/api/v1/system/qr/viewer.svg").status_code == 200
    assert viewer.get("/api/v1/system/qr/viewer.svg").status_code == 404


def test_refresh_endpoint(admin, viewer, fake_net):
    fake_net.interfaces = [Interface(17, "WLAN", "Intel", "wifi", "10.0.0.5", 24, "Neu", "Private")]
    r = admin.post("/api/v1/system/network/refresh")
    assert r.json["data"]["ip"] == "10.0.0.5"
    assert viewer.post("/api/v1/system/network/refresh").status_code == 403


def test_new_ip_is_accepted_as_host_after_network_change(app, admin, fake_net):
    fake_net.interfaces = [Interface(17, "WLAN", "Intel", "wifi", "10.0.0.5", 24, "Neu", "Private")]
    admin.post("/api/v1/system/network/refresh")
    c = app.test_client()
    assert c.get("/api/v1/system/ping", headers={"Host": "10.0.0.5:5000"}).status_code == 200


def test_setup_script_makes_network_private_and_adds_private_rule():
    from app.network import firewall
    script = firewall.setup_script(5000, 17)
    assert "Set-NetConnectionProfile -InterfaceIndex 17 -NetworkCategory Private" in script
    assert "-Profile Private" in script and "-LocalPort 5000" in script
    assert "Public" not in script and "Any" not in script
    only_profile = firewall.setup_script(None, 17)
    assert "New-NetFirewallRule" not in only_profile


def test_firewall_allow_switches_only_a_public_primary_network(admin, ctx, fake_net, monkeypatch):
    from app.network import firewall
    calls = []
    monkeypatch.setattr(firewall, "request_setup", lambda port, private_interface=None: calls.append((port, private_interface)) or True)
    fake_net.interfaces = [Interface(17, "WLAN", "Intel Wi-Fi", "wifi", LAN_IP, 24, "Heimnetz", "Public")]
    ctx.network.refresh(try_hotspot=False)
    r = admin.post("/api/v1/system/firewall/allow", {"make_private": True})
    assert r.status_code == 200 and r.json["data"]["make_private"] is True
    assert calls[-1][1] == 17
    fake_net.interfaces = [Interface(17, "WLAN", "Intel Wi-Fi", "wifi", LAN_IP, 24, "Heimnetz", "Private")]
    ctx.network.refresh(try_hotspot=False)
    r = admin.post("/api/v1/system/firewall/allow", {"make_private": True})
    # Netzwerk ist schon privat und ohne EXE gibt es keine Regel → nichts zu tun
    assert r.status_code == 422 and len(calls) == 1


def test_firewall_allow_is_admin_only(controller):
    assert controller.post("/api/v1/system/firewall/allow", {"make_private": True}).status_code == 403


def test_setup_script_replaces_block_rules_of_this_program_only():
    from app.network import firewall
    script = firewall.setup_script(5000, None)
    assert script.index("Get-NetFirewallApplicationFilter -Program") < script.index("New-NetFirewallRule")
    assert "Remove-NetFirewallRule" in script and "-DisplayName" in script.split("New-NetFirewallRule")[1]
    assert "EncodedCommand" not in script


def test_firewall_allow_never_switches_profile_in_hotspot_mode(admin, ctx, fake_net, monkeypatch):
    from app.network import firewall
    calls = []
    monkeypatch.setattr(firewall, "request_setup", lambda port, private_interface=None: calls.append(private_interface) or True)
    fake_net.interfaces = [Interface(17, "WLAN", "Intel Wi-Fi", "wifi", LAN_IP, 24, "Café", "Public")]
    ctx.network.refresh(try_hotspot=False)
    ctx.network.state.mode = "hotspot"
    r = admin.post("/api/v1/system/firewall/allow", {"make_private": True})
    assert r.status_code == 422 and calls == []
