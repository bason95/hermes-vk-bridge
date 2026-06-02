import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "hermes_vk_bridge.py"


def load_bridge():
    spec = importlib.util.spec_from_file_location("hermes_vk_bridge", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_simple_chat_uses_quick_toolset():
    bridge = load_bridge()
    toolsets, mode = bridge.select_toolsets("привет")
    assert mode == "quick"
    assert toolsets == "clarify"


def test_install_request_uses_full_toolset_with_terminal():
    bridge = load_bridge()
    toolsets, mode = bridge.select_toolsets("Давай поставим crawl4ai")
    assert mode == "full"
    assert "terminal" in toolsets.split(",")
    assert "file" in toolsets.split(",")


def test_full_and_quick_sessions_are_separate(monkeypatch):
    bridge = load_bridge()
    captured = []

    class DummyProc:
        returncode = 0
        def __init__(self, cmd, **kwargs):
            captured.append(cmd)
        def communicate(self, timeout=None):
            return "OK", ""

    monkeypatch.setattr(bridge.subprocess, "Popen", DummyProc)
    assert bridge.ask_hermes(123, "hello", toolsets="clarify", mode="quick") == "OK"
    assert bridge.ask_hermes(123, "code", toolsets=bridge.VK_FULL_TOOLSETS, mode="full") == "OK"
    assert captured[0][captured[0].index("-c") + 1].endswith("_quick")
    assert captured[1][captured[1].index("-c") + 1].endswith("_full")


def test_access_control_is_default_deny():
    bridge = load_bridge()
    assert bridge.is_allowed({}, peer_id=123, from_id=123) is False


def test_access_control_allows_explicit_user():
    bridge = load_bridge()
    env = {"VK_ALLOWED_USERS": "111, 222"}
    assert bridge.is_allowed(env, peer_id=123, from_id=222) is True
    assert bridge.is_allowed(env, peer_id=123, from_id=333) is False


def test_access_control_allow_all_requires_explicit_opt_in():
    bridge = load_bridge()
    assert bridge.is_allowed({"VK_ALLOW_ALL_USERS": "1"}, peer_id=999, from_id=999) is True


def test_approval_code_parser_and_state_approval():
    bridge = load_bridge()
    assert bridge.parse_approval_code('/approve VK-1234') == 'VK-1234'
    state = {"approved_users": []}
    bridge.approve_user(state, peer_id=123, from_id=456)
    assert bridge.is_allowed({}, peer_id=123, from_id=456, state=state) is True
    assert bridge.is_allowed({}, peer_id=789, from_id=789, state=state) is False
