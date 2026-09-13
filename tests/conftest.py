import importlib
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Use the real AstrBot API; only platform delivery and server transport are simulated.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))
_original_cwd = Path.cwd()
_runtime_dir = TemporaryDirectory(prefix="queqiao-tests-")
try:
    os.chdir(_runtime_dir.name)
    plugin = importlib.import_module(f"{PLUGIN_ROOT.name}.main")
finally:
    os.chdir(_original_cwd)


@pytest.fixture
def context():
    return SimpleNamespace(send_message=AsyncMock(return_value=True))


@pytest.fixture
def config():
    return {
        "queqiao_server": {
            "server_name": "Server",
            "server_uri": "ws://127.0.0.1:8080/minecraft/ws",
            "access_token": None,
        },
        "notification": {
            "enabled_events": ["玩家聊天|PlayerChatEvent"],
            "umo_list": ["test:GroupMessage:1"],
            "min_merge_window": 10,
            "max_merge_window": 60,
        },
    }


@pytest.fixture(autouse=True)
def local_transport_bypasses_proxy(monkeypatch):
    # websockets >= 15 honors environment proxies; loopback tests must stay local.
    monkeypatch.chdir(_runtime_dir.name)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


def pytest_unconfigure(config):
    os.chdir(_original_cwd)
    _runtime_dir.cleanup()
