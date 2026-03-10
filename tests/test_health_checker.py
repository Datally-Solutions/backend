import importlib.util
import sys
import os
from unittest.mock import patch


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hc = load_module(
    "health_checker_main",
    os.path.join(
        os.path.dirname(__file__), "..", "functions", "health_checker", "main.py"
    ),
)

check_no_pee = hc.check_no_pee
check_no_poop = hc.check_no_poop
check_weight_change = hc.check_weight_change
_process_alert = hc._process_alert

# ─── _identify_cat (shared logic) ────────────────────────────────────────────

CATS = [
    {"name": "Sully", "weight_kg": 3.5, "household_id": "hh1", "member_uids": []},
    {"name": "Krokmou", "weight_kg": 7.5, "household_id": "hh1", "member_uids": []},
]

# ─── check_no_pee ─────────────────────────────────────────────────────────────


def test_check_no_pee_triggers_alert():
    with patch.object(hc, "_hours_since_last_action", return_value=25.0), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_no_pee(CATS[0])
        mock_process.assert_called_once()
        assert mock_process.call_args[1]["alert_type"] == "no_pee_24h"
        assert mock_process.call_args[1]["severity"] == "critical"


def test_check_no_pee_no_alert_under_threshold():
    with patch.object(hc, "_hours_since_last_action", return_value=10.0), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_no_pee(CATS[0])
        mock_process.assert_not_called()


def test_check_no_pee_no_data():
    with patch.object(hc, "_hours_since_last_action", return_value=None), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_no_pee(CATS[0])
        mock_process.assert_not_called()


def test_check_no_pee_exactly_at_threshold():
    with patch.object(hc, "_hours_since_last_action", return_value=24.0), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_no_pee(CATS[0])
        mock_process.assert_called_once()


def test_check_no_poop_triggers_alert():
    with patch.object(hc, "_hours_since_last_action", return_value=50.0), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_no_poop(CATS[0])
        mock_process.assert_called_once()
        assert mock_process.call_args[1]["alert_type"] == "no_poop_48h"
        assert mock_process.call_args[1]["severity"] == "critical"


def test_check_no_poop_no_alert_under_threshold():
    with patch.object(hc, "_hours_since_last_action", return_value=24.0), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_no_poop(CATS[0])
        mock_process.assert_not_called()


def test_check_no_poop_no_data():
    with patch.object(hc, "_hours_since_last_action", return_value=None), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_no_poop(CATS[0])
        mock_process.assert_not_called()


def test_check_weight_change_triggers_alert():
    with patch.object(hc, "_get_avg_weight", side_effect=[3.0, 3.5]), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_weight_change(CATS[0])
        mock_process.assert_called_once()
        assert mock_process.call_args[1]["alert_type"] == "weight_change_10pct"
        assert mock_process.call_args[1]["severity"] == "warning"


def test_check_weight_change_no_alert_small_change():
    with patch.object(hc, "_get_avg_weight", side_effect=[3.4, 3.5]), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_weight_change(CATS[0])
        mock_process.assert_not_called()


def test_check_weight_change_no_data():
    with patch.object(hc, "_get_avg_weight", side_effect=[None, None]), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_weight_change(CATS[0])
        mock_process.assert_not_called()


def test_check_weight_change_partial_data():
    with patch.object(hc, "_get_avg_weight", side_effect=[3.5, None]), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_weight_change(CATS[0])
        mock_process.assert_not_called()


def test_check_weight_increase_triggers_alert():
    with patch.object(hc, "_get_avg_weight", side_effect=[4.0, 3.5]), patch.object(
        hc, "_process_alert"
    ) as mock_process:
        check_weight_change(CATS[0])
        mock_process.assert_called_once()
        assert "pris" in mock_process.call_args[1]["description"]
