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
