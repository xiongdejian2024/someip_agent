"""将虚拟网实际 PCAP 交给产品原生导入器，与独立抓包审计的人工黄金向量对照。"""

from __future__ import annotations

import argparse
import io
import json
import logging
from collections import Counter
from decimal import Decimal
from pathlib import Path

import dpkt
from someip_agent.config import Settings
from someip_agent.pcap.importer import PcapImporter

logger = logging.getLogger(__name__)


def audit(path: Path, reference: dict, directory: Path) -> dict:
    with path.open("rb") as source:
        reader = dpkt.pcap.Reader(source)
        link_type = reader.datalink()
        frames = list(reader)
    importer = PcapImporter(Settings(_env_file=None, data_dir=directory / "runtime"))
    result, messages = importer.parse(path.read_bytes(), path.name)
    if result.packet_count != len(frames) or result.captured_bytes != sum(
        len(data) for _, data in frames
    ):
        raise AssertionError("原生导入的原始帧数或字节数与独立读取结果不一致")
    if result.runtime != "vsomeip" or result.link_type != link_type or not messages:
        raise AssertionError("虚拟网文件未由真实原生运行时完整导入")
    counts = Counter()
    capture_counts = Counter()
    for message in messages:
        metadata = message.metadata
        if (
            metadata["runtime"] != "vsomeip"
            or metadata["observation"] != "pcap_import"
            or metadata["wire_verified"]
        ):
            raise AssertionError("离线消息来源被错误标记")
        number = metadata["pcap_frame"]
        if not 1 <= number <= len(frames):
            raise AssertionError("原生消息关联到不存在的原始帧")
        expected_ns = int(Decimal(str(frames[number - 1][0])) * 1_000_000_000)
        # 独立 dpkt 读取微秒 PCAP 时使用浮点；只容许其一个微秒以内的表示误差。
        if abs(metadata["native_timestamp_ns"] - expected_ns) > 1000:
            raise AssertionError("原生消息的软件时间不属于其完成帧")
        counts[
            (
                message.transport,
                message.service_id,
                message.method_id,
                message.message_type,
                message.payload_hex,
                message.client_id,
                message.source,
                message.destination,
                message.interface_version,
                message.return_code,
            )
        ] += 1
        if (message.service_id, message.method_id, message.payload_hex) == (
            0x4567,
            0x8003,
            "41280000",
        ):
            direction = (
                "request"
                if message.destination.endswith(":30608")
                else "reply"
                if message.source.endswith(":30608")
                else None
            )
            if direction:
                kind = message.transport + (
                    "6" if message.source.startswith("[") else ""
                )
                capture_counts[kind + "_" + direction] += 1

    checks = []
    for name, default_service in (
        ("golden_packets", 0x1234),
        ("arxml_catalog_packets", 0x1234),
        ("sat_wti_packets", None),
        ("sat_recovery_packets", 0x1234),
        ("service_api_packets", 0x1234),
        ("arxml_composite_packets", 0x3456),
    ):
        for expected in reference[name]:
            service = expected.get("service_id", default_service)
            count = sum(
                amount
                for key, amount in counts.items()
                if key[:5]
                == (
                    expected["transport"],
                    service,
                    expected["method_id"],
                    expected["message_type"],
                    expected["payload_hex"],
                )
                and ("client_id" not in expected or key[5] == expected["client_id"])
                and (
                    name != "arxml_catalog_packets"
                    or any(
                        endpoint.endswith(":" + str(port))
                        for endpoint in key[6:8]
                        for port in (
                            (30505, 30506)
                            if expected["byte_order"] == "little"
                            else (30503, 30504)
                        )
                    )
                )
                and (
                    name not in {"service_api_packets", "arxml_composite_packets"}
                    or (
                        key[8:] == (1, 0)
                        and key[6].rsplit(":", 1)[0] == expected["source_host"]
                        and key[7].rsplit(":", 1)[0] == expected["destination_host"]
                        and any(
                            endpoint.endswith(":" + str(expected["service_port"]))
                            for endpoint in key[6:8]
                        )
                    )
                )
                and (
                    name != "sat_recovery_packets"
                    or (key[6] if expected["message_type"] == 0 else key[7]).rsplit(
                        ":", 1
                    )[0]
                    == (
                        "10.77.0.1"
                        if expected["recovered_role"] == "client"
                        else "10.77.0.2"
                    )
                )
            )
            if not count:
                raise AssertionError(
                    f"原生离线导入缺少 {name} 人工黄金消息: {expected}"
                )
            checks.append({"group": name, **expected, "native_messages": count})
    if not sum(
        amount
        for key, amount in counts.items()
        if key[:5] == ("udp", 0x1234, 0x8002, 2, "41480000")
    ):
        raise AssertionError("原生导入缺少真实后端周期发生器黄金事件")
    for name, minimum in reference["native_passive_capture_packets"].items():
        if capture_counts[name] < minimum:
            raise AssertionError(f"原生导入缺少双向 IPv4/IPv6 捕获黄金消息: {name}")
    for name in (
        "OfferService",
        "StopOfferService",
        "SubscribeEventgroup",
        "SubscribeEventgroupAck",
    ):
        if not result.sd_entry_counts[name]:
            raise AssertionError("原生导入缺少真实 SD 状态: " + name)

    fragment_checks = []
    for expected in reference["ipv4_fragment_packets"]:
        selected = []
        protocol = 17 if expected["transport"] == "udp" else 6
        for timestamp, data in frames:
            network = dpkt.ethernet.Ethernet(data).data
            if (
                isinstance(network, dpkt.ip.IP)
                and (network.mf or network.offset)
                and network.src == bytes((10, 77, 0, 2))
                and network.dst == bytes((10, 77, 0, 1))
                and network.id == expected["ip_id"]
                and network.p == protocol
            ):
                selected.append((timestamp, data))
        if len(selected) != expected["captured_fragments"]:
            raise AssertionError("分片审计原始帧数量不一致")
        # 各 live 捕获用例拥有独立重组上下文；离线逐例保持相同边界。
        # 多个 TCP 用例复用同四元组和序号，整份文件不能把重传伪计成新消息。
        output = io.BytesIO()
        writer = dpkt.pcap.Writer(output, linktype=link_type)
        for timestamp, data in selected:
            writer.writepkt(data, ts=timestamp)
        partial, decoded = importer.parse(
            output.getvalue(), expected["profile"] + ".pcap"
        )
        good = expected["expected_outcome"] in {
            "reassembled",
            "expired_then_reassembled",
        }
        if good:
            if (
                len(decoded) != 1
                or partial.reassembled_datagrams != 1
                or decoded[0].payload_hex != "41280000"
            ):
                raise AssertionError(
                    "真实分片未输出唯一人工黄金消息: " + expected["profile"]
                )
            metadata = decoded[0].metadata
            if not metadata["ip_reassembled"] or metadata["ip_fragment_count"] != 2:
                raise AssertionError("真实分片的原生来源统计错误")
            if expected["profile"] == "syn_payload" and metadata["tcp_partial"]:
                raise AssertionError("实际 SYN 开始的流被错误标记为中途捕获")
        elif decoded or not partial.fragment_error_count:
            raise AssertionError("残缺或冲突分片未隔离: " + expected["profile"])
        if expected["expected_outcome"] == "expired_then_reassembled" and not any(
            "30 秒" in error for error in partial.errors
        ):
            raise AssertionError("真实文件时间跨越 30 秒未执行原生分片过期")
        fragment_checks.append(
            {
                **expected,
                "native_messages": len(decoded),
                "native_fragment_errors": partial.fragment_error_count,
            }
        )
    logger.info(
        "原生离线虚拟网审计通过：%s 帧，%s 条消息，%s 组黄金向量，%s 组分片",
        result.packet_count,
        len(messages),
        len(checks),
        len(fragment_checks),
    )
    return {
        "status": "verified",
        "statistics": result.model_dump(mode="json"),
        "golden_messages": checks,
        "native_passive_capture_messages": dict(capture_counts),
        "fragment_imports": fragment_checks,
        "scope": "真实虚拟以太网文件的原生离线解码；不代表线速、性能或任意 OEM 布局",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="虚拟网 PCAP 原生离线产品路径审计")
    parser.add_argument("pcap", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        result = audit(
            args.pcap,
            json.loads(args.reference.read_text()),
            args.output.parent / "offline",
        )
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        logger.exception("原生离线虚拟网审计失败")
        raise


if __name__ == "__main__":
    main()
