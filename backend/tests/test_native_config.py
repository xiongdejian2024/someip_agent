import json

import pytest
from fastapi.testclient import TestClient

from someip_agent.config import Settings
from someip_agent.domain.models import SimulationConfig
from someip_agent.main import create_app
from someip_agent.runtime.native_config import (
    SimulationPermissionError,
    validate_network,
    write_inputs,
)


def test_internal_config_disables_external_sd(tmp_path):
    catalog, config, name = write_inputs(
        tmp_path / "native",
        "test",
        SimulationConfig(service_id=0x1234, method_id=0x8001),
        Settings(_env_file=None, data_dir=tmp_path),
    )
    runtime = json.loads(config.read_text())
    assert runtime["service-discovery"]["enable"] is False
    assert "unreliable" not in runtime["services"][0]
    assert runtime["routing"] == name
    assert (
        json.loads(catalog.read_text())["Simulation"]["events"]["UpdateSampleEvent"]["id"] == 0x8001
    )


def test_subscription_network_requires_host_permission(tmp_path):
    config = SimulationConfig(
        service_id=0x1234,
        method_id=0x8001,
        transport="udp",
        destination_host="10.77.0.2",
        destination_port=30501,
        enable_sd=False,
    )
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        native_unicast="10.77.0.1",
        network_send_enabled=True,
        allowed_destinations=["10.77.0.2:30501"],
    )
    with pytest.raises(SimulationPermissionError, match="主机级"):
        validate_network(config, settings)
    settings.allowed_destinations = ["10.77.0.2"]
    validate_network(config, settings)


def test_missing_native_binary_is_explicit_service_unavailable(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, native_binary=str(tmp_path / "不存在的二进制"))
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/simulation/start", json={"service_id": 0x1234, "method_id": 0x8001}
        )
        assert response.status_code == 503
        assert "未找到" in response.json()["detail"]
        assert client.get("/api/v1/simulation").json() == []
