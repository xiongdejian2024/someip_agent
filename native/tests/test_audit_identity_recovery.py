"""篡改真实故障抓包副本必须被拒绝；不把注入副本算作正常线上证据。"""

import json
import os
from pathlib import Path

import dpkt
import pytest
from audit_identity_recovery import audit


@pytest.mark.parametrize(
    "change",
    ["client", "session", "payload", "missing", "duplicate", "phase", "handshake"],
)
def test_recovery_audit_rejects_tampered_real_capture(tmp_path, change):
    source = Path(
        os.environ.get("SOMEIP_AGENT_AUDIT_PCAP", "build/virtual-evidence/soa.pcap")
    )
    assert source.is_file(), "缺少真实故障抓包，不能跳过审计验收"
    with source.open("rb") as stream:
        reader = dpkt.pcap.Reader(stream)
        frames, link_type = list(reader), reader.datalink()
    target_flow = None
    if change == "handshake":
        for _timestamp, frame in frames:
            network = dpkt.ethernet.Ethernet(frame).data
            if isinstance(network, dpkt.ip.IP) and isinstance(
                network.data, dpkt.tcp.TCP
            ):
                layer = network.data
                if layer.dport == 30761 and layer.data[:4] == bytes.fromhex("67900001"):
                    target_flow = (network.src, network.dst, layer.sport, layer.dport)
                    break
        assert target_flow is not None, "缺少真正承载业务请求的 TCP 流"
    changed, modified = [], False
    for timestamp, frame in frames:
        network = dpkt.ethernet.Ethernet(frame).data
        if isinstance(network, dpkt.ip.IP):
            layer = network.data
            if change == "handshake":
                if (
                    isinstance(layer, dpkt.tcp.TCP)
                    and (network.src, network.dst, layer.sport, layer.dport)
                    == target_flow
                    and layer.flags & dpkt.tcp.TH_SYN
                ):
                    modified = True
                    continue
            elif (
                not modified
                and isinstance(layer, dpkt.udp.UDP)
                and layer.dport == 30760
                and len(layer.data) == 18
                and layer.data[14] == 0
            ):
                modified = True
                if change == "missing":
                    continue
                if change == "duplicate":
                    changed.append((timestamp, frame))
                elif change == "phase":
                    report = json.loads(
                        (source.parent / "identity-recovery-30760.json").read_text()
                    )
                    timestamp = report["fault_at"] + 1
                else:
                    raw = bytearray(frame)
                    offset = 14 + network.hl * 4 + 8
                    raw[
                        offset + {"client": 9, "session": 11, "payload": 17}[change]
                    ] ^= 1
                    frame = bytes(raw)
        changed.append((timestamp, frame))
    assert modified, "缺少可篡改的真实故障恢复流"
    path = tmp_path / (change + ".pcap")
    with path.open("wb") as stream:
        writer = dpkt.pcap.Writer(
            stream, linktype=link_type, snaplen=max(len(frame) for _, frame in changed)
        )
        for timestamp, frame in changed:
            writer.writepkt(frame, ts=timestamp)
    with pytest.raises(AssertionError, match="Client ID|黄金|错配|阶段|握手"):
        audit(path, source.parent, tmp_path)
