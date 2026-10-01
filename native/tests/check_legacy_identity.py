"""用指定哈希的真实旧桥接二进制验证能力拒绝；不生成或伪装旧运行时。"""

import argparse
import hashlib
import json
import logging
from pathlib import Path

from someip_agent.soa.operator import NativeOperationError, SOAOperator
from someip_agent.soa.partner import S2sBaseClass

logger = logging.getLogger(__name__)


def check(binary: Path, checksum: str, evidence: Path):
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if digest != checksum:
        raise AssertionError("旧二进制哈希与指定验收对象不一致")
    evidence.mkdir(parents=True, exist_ok=True)
    catalog_path = Path(__file__).parent / "catalog.json"
    config = json.loads((Path(__file__).parent / "client.json").read_text())
    config.update(
        {
            "unicast": "127.0.0.1",
            "network": "soa-legacy-identity-check",
            "routing": "legacy_identity",
            "applications": [
                {"name": "legacy_identity", "id": "0x7940"},
                {"name": "legacy_consumer", "id": "0x7941"},
            ],
        }
    )
    config_path = evidence / "config.json"
    config_path.write_text(json.dumps(config))
    operator = SOAOperator(
        "legacy_identity",
        operator_port=0,
        binary=binary,
        catalog=catalog_path,
        config=config_path,
        log_path=evidence / "legacy-native.log",
    )
    with S2sBaseClass(
        {"DoorService": {"role": "client"}}, operator=operator, auto_restart=False
    ) as default:
        process = operator.process
        assert process is not None and process.poll() is None
        pong = operator.send_request("ping")
        assert pong["runtime"] == "vsomeip" and pong["protocol"] == 1
        assert "member_application_identity_v1" not in pong.get("capabilities", [])
        # 证明旧桥接会接受新增字段，而非只用 mock 声称其不兼容。
        ignored = {
            "Ignored": {
                "service": "DoorService",
                "role": "client",
                "application_name": "legacy_consumer",
            }
        }
        raw = operator.send_request("start_config_get_args", ignored)
        assert "Ignored_client" in raw
        operator.send_request(
            "start_config_get_args",
            {"Ignored": {"role": "client", "enable": "disable"}},
        )
        before = operator.send_request("running_service")
        guarded_operator = SOAOperator(
            host=operator.host, operator_port=operator.operator_port
        )
        with S2sBaseClass(
            operator=guarded_operator, attach=True, auto_start=False
        ) as guarded:
            try:
                guarded.start_soa(ignored)
            except NativeOperationError:
                logger.info("真实旧二进制已在成员启动前被能力门禁拒绝", exc_info=True)
            else:
                raise AssertionError("新 Python 适配器静默接受了旧二进制的身份降级")
            assert not guarded.partner_infos and not guarded._member_configs
        assert process.poll() is None and operator.process is process
        assert operator.send_request("running_service") == before
        assert default.partner_infos["DoorService_client"].running
    assert process.poll() == 0
    result = {
        "legacy_binary_sha256": digest,
        "legacy_ping": pong,
        "default_dictionary_compatible": True,
        "legacy_raw_selector_accepted": True,
        "named_selector_rejected_before_mutation": True,
        "attached_process_not_terminated": True,
        "owned_cleanup_exit": process.poll(),
    }
    (evidence / "legacy-identity.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    logger.info("真实旧二进制兼容性验收通过: %s", result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        check(args.binary, args.sha256, args.evidence)
    except Exception:
        logger.exception("真实旧二进制身份兼容性验收失败")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
