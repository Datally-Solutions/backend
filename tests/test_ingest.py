import importlib.util
import sys
import os


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ─── Import functions to test ─────────────────────────────────────────────────
# We import only pure functions — no GCP clients instantiated
ingest = load_module(
    "ingest_main",
    os.path.join(os.path.dirname(__file__), "..", "functions", "ingest", "main.py"),
)

_identify_cat = ingest._identify_cat
_classify_action = ingest._classify_action
_parse_payload = ingest._parse_payload
# ─── _identify_cat ────────────────────────────────────────────────────────────

CATS = [
    {"name": "Sully", "weight_kg": 3.5},
    {"name": "Krokmou", "weight_kg": 7.5},
]


def test_identify_sully_exact():
    cat = _identify_cat(3.5, CATS)
    assert cat["name"] == "Sully"


def test_identify_krokmou_exact():
    cat = _identify_cat(7.5, CATS)
    assert cat["name"] == "Krokmou"


def test_identify_sully_within_tolerance():
    # 3.5 * 0.30 = 1.05 tolerance → 2.5 to 4.55
    cat = _identify_cat(3.0, CATS)
    assert cat["name"] == "Sully"


def test_identify_krokmou_within_tolerance():
    # 7.5 * 0.30 = 2.25 tolerance → 5.25 to 9.75
    cat = _identify_cat(8.0, CATS)
    assert cat["name"] == "Krokmou"


def test_identify_unknown_cat():
    # 5.0 — equidistant, outside both tolerances
    cat = _identify_cat(5.0, CATS)
    assert cat is None


def test_identify_empty_cats():
    cat = _identify_cat(7.5, [])
    assert cat is None


def test_identify_cats_with_zero_weight():
    cats = [{"name": "Ghost", "weight_kg": 0}]
    cat = _identify_cat(3.5, cats)
    assert cat is None


def test_identify_single_cat():
    cats = [{"name": "Solo", "weight_kg": 5.0}]
    cat = _identify_cat(5.0, cats)
    assert cat["name"] == "Solo"


# ─── _classify_action ────────────────────────────────────────────────────────

SULLY = {"name": "Sully", "weight_kg": 3.5}
KROKMOU = {"name": "Krokmou", "weight_kg": 7.5}


def test_classify_simple_visit_sully():
    # scale: 3.5/4 = 0.875 → visite_max = 13.1g
    action, alerte = _classify_action(SULLY, 5.0, 20)
    assert action == "Simple visite"
    assert alerte is None


def test_classify_simple_visit_long_scratching():
    action, alerte = _classify_action(SULLY, 5.0, 100)
    assert action == "Simple visite"
    assert alerte is not None
    assert "Grattage" in alerte


def test_classify_pipi_sully():
    # visite_max=13.1g, pipi_max=43.75g → pipi range: 13.1-43.75g
    action, alerte = _classify_action(SULLY, 25.0, 60)
    assert action == "Pipi 🟡"
    assert alerte is None


def test_classify_petit_pipi_sully():
    action, alerte = _classify_action(SULLY, 25.0, 130)
    assert action == "Petit Pipi 🟡"
    assert alerte is not None


def test_classify_caca_sully():
    # above pipi_max + long duration
    action, alerte = _classify_action(SULLY, 100.0, 120)
    assert action == "Caca 🟤"


def test_classify_gros_pipi_sully():
    # above pipi_max + short duration
    action, alerte = _classify_action(SULLY, 100.0, 30)
    assert action == "Gros Pipi 🟡"


def test_classify_krokmou_pipi():
    # scale: 7.5/4 = 1.875 → pipi_max = 93.75g
    action, alerte = _classify_action(KROKMOU, 50.0, 60)
    assert action == "Pipi 🟡"


def test_classify_krokmou_caca():
    action, alerte = _classify_action(KROKMOU, 150.0, 120)
    assert action == "Caca 🟤"


def test_classify_negative_delta():
    action, alerte = _classify_action(SULLY, -10.0, 30)
    assert action == "Simple visite"
    assert alerte is None


def test_classify_long_session_alert():
    action, alerte = _classify_action(SULLY, 25.0, 300)
    assert alerte is not None
    assert "Long" in alerte


def test_classify_unknown_cat_fallback():
    # None cat uses default thresholds
    action, alerte = _classify_action(None, 5.0, 20)
    assert action == "Simple visite"


def test_classify_unknown_cat_pipi():
    action, alerte = _classify_action(None, 30.0, 60)
    assert action == "Pipi 🟡"


# ─── _parse_payload ───────────────────────────────────────────────────────────


class MockRequest:
    def __init__(self, data):
        self._data = data

    def get_json(self, force=False):
        return self._data


def test_parse_valid_payload():
    req = MockRequest(
        {
            "device_id": "30251CB7B3F8",
            "entry_weight_kg": 7.2,
            "exit_weight_delta_g": 148.3,
            "duration_seconds": 143,
        }
    )
    row = _parse_payload(req)
    assert row is not None
    assert row["device_id"] == "30251CB7B3F8"
    assert row["entry_weight_kg"] == 7.2
    assert row["exit_weight_delta_g"] == 148.3
    assert row["duration_seconds"] == 143


def test_parse_missing_fields():
    req = MockRequest({"device_id": "abc"})
    row = _parse_payload(req)
    # Should not crash — missing fields use defaults
    assert row is not None
    assert row["entry_weight_kg"] == 0.0


def test_parse_empty_payload():
    req = MockRequest(None)
    row = _parse_payload(req)
    assert row is None


def test_parse_invalid_types():
    req = MockRequest(
        {
            "device_id": "abc",
            "entry_weight_kg": "not_a_float",
            "exit_weight_delta_g": 10.0,
            "duration_seconds": 60,
        }
    )
    row = _parse_payload(req)
    assert row is None
