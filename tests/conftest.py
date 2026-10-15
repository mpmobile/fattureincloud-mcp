import importlib
import sys
from types import SimpleNamespace

import pytest


def load_server():
    """Import the server package fresh, so it reads the env vars set by the test.

    Returns a facade exposing what tests patch or call: the SDK clients (shared
    instances, so `patch.object(server.issued_api, ...)` reaches every tool),
    the cached lookups and `call_tool`.
    """
    for mod in [m for m in sys.modules if m.startswith("fattureincloud_mcp")]:
        del sys.modules[mod]
    app = importlib.import_module("fattureincloud_mcp.app")
    fic = importlib.import_module("fattureincloud_mcp.fic")
    lookups = importlib.import_module("fattureincloud_mcp.lookups")
    return SimpleNamespace(
        call_tool=app.mcp.call_tool,
        issued_api=fic.issued_api, received_api=fic.received_api, clients_api=fic.clients_api,
        info_api=fic.info_api, einvoice_api=fic.einvoice_api, companies_api=fic.companies_api,
        get_client_by_id=lookups.get_client_by_id,
        get_ei_code_for_client=lookups.get_ei_code_for_client,
        build_entity_from_client=lookups.build_entity_from_client,
        fetch_cost_centers=lookups.fetch_cost_centers,
        fetch_revenue_centers=lookups.fetch_revenue_centers,
    )


@pytest.fixture
def fic_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIC_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("FIC_CACHE_DISABLED", raising=False)
    monkeypatch.setenv("FIC_ACCESS_TOKEN", "test_token")
    monkeypatch.setenv("FIC_COMPANY_ID", "100")
    monkeypatch.setenv("FIC_SENDER_EMAIL", "test@example.invalid")


@pytest.fixture
def server_module(fic_env):
    return load_server()
