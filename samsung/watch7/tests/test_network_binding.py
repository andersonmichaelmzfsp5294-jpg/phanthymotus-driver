"""MCP should be loopback-only by default; bridge test exposure is opt-in."""
import pytest

from main import resolve_mcp_bind_host, resolve_mcp_server_name, resolve_safe_namespace


def test_mcp_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("WATCH7_MCP_BIND_HOST", raising=False)
    monkeypatch.delenv("WATCH7_PRIVATE_DOCKER_TEST", raising=False)
    assert resolve_mcp_bind_host() == "127.0.0.1"


def test_mcp_bridge_binding_is_explicitly_opted_in(monkeypatch):
    monkeypatch.setenv("WATCH7_MCP_BIND_HOST", "0.0.0.0")
    monkeypatch.delenv("WATCH7_PRIVATE_DOCKER_TEST", raising=False)
    with pytest.raises(RuntimeError, match="PRIVATE_DOCKER_TEST"):
        resolve_mcp_bind_host()
    monkeypatch.setenv("WATCH7_PRIVATE_DOCKER_TEST", "1")
    assert resolve_mcp_bind_host() == "0.0.0.0"


def test_mcp_cannot_bind_arbitrary_lan_ip(monkeypatch):
    monkeypatch.setenv("WATCH7_PRIVATE_DOCKER_TEST", "1")
    monkeypatch.setenv("WATCH7_MCP_BIND_HOST", "192.168.1.88")
    with pytest.raises(RuntimeError):
        resolve_mcp_bind_host()

def test_lab_mcp_names_cannot_collide_with_production(monkeypatch):
    monkeypatch.delenv("WATCH7_TEST_MCP_SERVER_NAME", raising=False)
    assert resolve_mcp_server_name({"name":"Samsung Galaxy Watch7 Bundle"}) == "Samsung Galaxy Watch7 Bundle"
    monkeypatch.setenv("WATCH7_TEST_MCP_SERVER_NAME", "samsung-watch7-s6-lab")
    monkeypatch.delenv("WATCH7_PRIVATE_DOCKER_TEST", raising=False)
    with pytest.raises(RuntimeError):
        resolve_mcp_server_name({})
    monkeypatch.setenv("WATCH7_PRIVATE_DOCKER_TEST", "1")
    assert resolve_mcp_server_name({}) == "samsung-watch7-s6-lab"
    monkeypatch.setenv("WATCH7_TEST_MCP_SERVER_NAME", "12-invalid")
    with pytest.raises(ValueError):
        resolve_mcp_server_name({})


def test_docker_generated_numeric_namespace_is_ros2_valid(monkeypatch):
    monkeypatch.delenv("ROS_NAMESPACE", raising=False)
    monkeypatch.setattr("main.resolve_namespace", lambda cfg: "4911de9")
    assert resolve_safe_namespace({}) == "watch7_4911de9"
    monkeypatch.setattr("main.resolve_namespace", lambda cfg: "watch7_lab")
    assert resolve_safe_namespace({}) == "watch7_lab"
