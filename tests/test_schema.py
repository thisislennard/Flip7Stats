import pytest

from app.config import Settings
from app.security import validation as v
from app.security.schema import ValidationError


def errors(model, data):
    with pytest.raises(ValidationError) as exc:
        model.model_validate(data)
    return {(".".join(map(str, e["loc"])), e["type"]) for e in exc.value.errors()}


def test_points_are_strict_integers():
    base = {"idempotency_key": "abcdefgh", "round_number": 1}
    for bad in (-1.0, 1.5, "10", True):
        assert ("scores.0.points", "int_type") in errors(v.RoundCreate, {**base, "scores": [{"player_id": 1, "points": bad}]})
    ok = v.RoundCreate.model_validate({**base, "scores": [{"player_id": 1, "points": None}, {"player_id": 2, "points": 0}]})
    assert [s.points for s in ok.scores] == [None, 0]


def test_unknown_missing_and_range_errors():
    assert ("extra", "extra_forbidden") in errors(v.PlayerCreate, {"display_name": "A", "extra": 1})
    assert ("display_name", "missing") in errors(v.PlayerCreate, {})
    assert ("display_name", "string_too_short") in errors(v.PlayerCreate, {"display_name": "   "})
    assert ("target_score", "less_than_equal") in errors(v.GameContinue, {"target_score": 10_001})
    assert ("role", "literal_error") in errors(v.TokenRotate, {"role": "admin"})
    assert ("idempotency_key", "string_pattern_mismatch") in errors(v.RoundUpdate, {"idempotency_key": "x", "scores": [{"player_id": 1, "points": 1}]})


def test_duplicate_players_and_list_lengths():
    assert ("player_ids", "value_error") in errors(v.NightCreate, {"player_ids": [1, 1]})
    assert ("player_ids", "too_short") in errors(v.NightCreate, {"player_ids": [1]})
    assert v.GameCreate.model_validate({}).player_ids == []


def test_strings_are_trimmed_and_dump_skips_none():
    p = v.PlayerCreate.model_validate({"display_name": "  Anna  "})
    assert p.display_name == "Anna"
    assert v.SettingsUpdate.model_validate({"port": 5001}).model_dump(exclude_none=True) == {"port": 5001}


def test_settings_defaults_ignore_unknown_and_reject_bad_values():
    s = Settings.model_validate({"port": 5050, "veraltet": True})
    assert s.port == 5050 and s.default_target_score == 200 and len(s.hotspot_password) >= 8
    assert "veraltet" not in s.model_dump()
    with pytest.raises(ValidationError):
        Settings.model_validate({"port": "5000"})


def test_firewall_removal_targets_only_this_program(monkeypatch):
    from app.network import firewall
    monkeypatch.setattr(firewall, "program_path", lambda: r"C:\Users\Test O'Brien\Flip7Live.exe")
    script = firewall.removal_script()
    assert "-Program 'C:\\Users\\Test O''Brien\\Flip7Live.exe'" in script and "Remove-NetFirewallRule" in script


def test_installer_version_compare():
    from app.desktop import installer
    assert installer._version_tuple("1.10.0") > installer._version_tuple("1.9.3")
    assert installer._version_tuple(None) < installer._version_tuple("0.0.1")
    assert installer.installed_exe().name == "Flip7Live.exe"


def test_settings_file_with_bom_is_accepted(tmp_path):
    from app.config import Paths, SettingsStore
    paths = Paths(tmp_path)
    paths.ensure()
    paths.settings_file.write_text('{"port": 5099}', encoding="utf-8-sig")
    store = SettingsStore(paths)
    assert store.value.port == 5099 and store.recovered_from is None
