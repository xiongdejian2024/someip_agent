"""独立人工选项黄金向量审计；不引用发送器、产品解析器或共享夹具生成预期值。"""

from __future__ import annotations

import socket
import struct
from collections import defaultdict

import dpkt

# 与发送端分离，逐项手工列出线上字节、IP ID 和预期解析结局。
GOLDEN_OPTIONS = (
    ("eol", "00000000", True),
    ("nop_eol", "01000000", True),
    ("copied_code0", "80020000", True),
    (
        "max_padding",
        "01000000000000000000000000000000000000000000000000000000000000000000000000000000",
        True,
    ),
    ("record_route", "0707040000000000", True),
    ("timestamp", "440c05000000000000000000", True),
    ("bad_padding", "00000100", False),
    ("missing_length", "0101019e", False),
    ("short_length", "9e010000", False),
    ("overrun", "9e050000", False),
)
SOURCE = socket.inet_aton("10.77.0.2")
DESTINATION = socket.inet_aton("10.77.0.1")
MESSAGE = bytes.fromhex("456780030000000c001100170101020041280000")


def option_key(network):
    if not isinstance(network, dpkt.ip.IP):
        return None
    if network.src != SOURCE or network.dst != DESTINATION:
        return None
    base = {17: 0x7810, 6: 0x7830}.get(network.p)
    if base is None:
        return None
    fragmented = bool(network.mf or network.offset)
    shift = 0 if fragmented else 0x100
    index = network.id - base - shift - 1
    if not 0 <= index < 10:
        return None
    if network.offset == 0:
        segment = network.data
        # Linux 普通流量会复用 16 位 IP ID。普通包/首片须同时匹配专用端口；
        # 尾片没有端口，仍按协议、方向、ID 和分片标志选择，严格核对实际序列。
        if not isinstance(segment, (dpkt.udp.UDP, dpkt.tcp.TCP)) or (
            segment.sport,
            segment.dport,
        ) != (41060 + index + (0 if fragmented else 20), 30618):
            return None
    return network.p, network.id


class OptionAudit:
    def __init__(self):
        self.packets = defaultdict(list)

    def observe(self, network, frame):
        key = option_key(network)
        if key is not None:
            # 本向量是无 VLAN 的 Ethernet IPv4；校验原始字节，不重新序列化后验和。
            assert frame[12:14] == b"\x08\x00", "IPv4 选项向量链路类型不符"
            header = frame[14 : 14 + network.hl * 4]
            assert len(frame) == 14 + network.len, "IPv4 选项帧截断或总长度不符"
            assert dpkt.in_cksum(header) == 0, "IPv4 选项原始头校验和错误"
            self.packets[key].append(
                (
                    network.offset * 8,
                    bool(network.mf),
                    header[20:],
                    frame[14 + len(header) :],
                )
            )

    def verify(self):
        checks = []
        for protocol, base, transport, split in (
            (17, 0x7810, "udp", 16),
            (6, 0x7830, "tcp", 24),
        ):
            for fragmented in (False, True):
                for index, (profile, options_hex, valid) in enumerate(GOLDEN_OPTIONS):
                    identifier = base + index + 1 + (0 if fragmented else 0x100)
                    pieces = self.packets[(protocol, identifier)]
                    expected_shape = (
                        [(split, False, b""), (0, True, bytes.fromhex(options_hex))]
                        if fragmented
                        else [(0, False, bytes.fromhex(options_hex))]
                    )
                    assert [piece[:3] for piece in pieces] == expected_shape, (
                        f"IPv4 选项帧序列/选项字节不符：{transport} {profile} 分片={fragmented}"
                    )
                    body = b"".join(
                        piece[3] for piece in sorted(pieces, key=lambda item: item[0])
                    )
                    segment = (
                        dpkt.udp.UDP(body) if protocol == 17 else dpkt.tcp.TCP(body)
                    )
                    port = 41060 + index + (0 if fragmented else 20)
                    assert (segment.sport, segment.dport) == (port, 30618), (
                        "IPv4 选项传输端口不符"
                    )
                    assert segment.data == MESSAGE, "IPv4 选项 SOME/IP 黄金字节不符"
                    assert len(body) == (28 if protocol == 17 else 40), (
                        "IPv4 选项传输长度不符"
                    )
                    if protocol == 17:
                        assert segment.ulen == 28 and segment.sum != 0, (
                            "IPv4 选项 UDP 长度或校验和缺失"
                        )
                    else:
                        assert (
                            segment.seq,
                            segment.ack,
                            segment.off,
                            segment.flags,
                        ) == (101, 701, 5, 24), "IPv4 选项 TCP 头黄金字段不符"
                    pseudo = (
                        SOURCE
                        + DESTINATION
                        + struct.pack("!BBH", 0, protocol, len(body))
                    )
                    assert dpkt.in_cksum(pseudo + body) == 0, (
                        "IPv4 选项传输层校验和错误"
                    )
                    if fragmented:
                        assert len(pieces[1][3]) == split, "IPv4 选项分片起止不连续"
                    checks.append(
                        {
                            "transport": transport,
                            "profile": profile,
                            "ip_id": identifier,
                            "fragmented": fragmented,
                            "captured_frames": len(pieces),
                            "options_hex": options_hex,
                            "source_port": port,
                            "expected_outcome": "decoded" if valid else "malformed",
                        }
                    )
        return checks
