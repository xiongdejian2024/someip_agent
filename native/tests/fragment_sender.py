"""只在隔离虚拟网注入真实 IPv4 片段；产品不调用此测试发送器。"""

import argparse
import logging
import socket

import dpkt

logger = logging.getLogger(__name__)
GOLDEN = bytes.fromhex("456780030000000c001100170101020041280000")
PROFILES = [
    "ordered",
    "reordered",
    "duplicate",
    "overlap",
    "missing",
    "options",
    "header_conflict",
]


def main():
    parser = argparse.ArgumentParser(description="IPv4 分片虚拟网验收发送器")
    parser.add_argument("transport", choices=["udp", "tcp"])
    parser.add_argument(
        "--id", type=lambda value: int(value, 0), help="复用已过期数据报的 IP 标识"
    )
    parser.add_argument("profile", choices=PROFILES)
    args = parser.parse_args()
    source, destination = socket.inet_aton("10.77.0.2"), socket.inet_aton("10.77.0.1")
    if args.transport == "udp":
        transport = dpkt.udp.UDP(sport=41014, dport=30614, data=GOLDEN)
        transport.ulen = len(transport)
        protocol, split = 17, 16
    else:
        transport = dpkt.tcp.TCP(
            sport=41014,
            dport=30614,
            seq=101,
            ack=701,
            flags=dpkt.tcp.TH_ACK | dpkt.tcp.TH_PUSH,
            data=GOLDEN,
        )
        protocol, split = 6, 24
    full = dpkt.ip.IP(src=source, dst=destination, p=protocol, ttl=64, data=transport)
    full.len = len(full)
    raw = bytes(full)[20:]
    identifier = (0x7710 if args.transport == "udp" else 0x7720) + (
        PROFILES.index(args.profile) + 1
    )
    if args.id is not None:
        if not 0 < args.id <= 65535:
            parser.error("IPv4 分片标识必须介于 1 和 65535")
        identifier = args.id
    pieces = [(0, raw[:split], True), (split, raw[split:], False)]
    if args.profile == "reordered":
        pieces.reverse()
    elif args.profile == "duplicate":
        pieces.insert(1, pieces[0])
    elif args.profile == "overlap":
        pieces[1] = (8, raw[8:], False)
    elif args.profile == "missing":
        pieces.pop()
    elif args.profile == "header_conflict":
        pieces.insert(1, pieces[0])
    try:
        with socket.socket(
            socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW
        ) as sender:
            sender.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
            sender.bind(("10.77.0.2", 0))
            for index, (offset, body, more) in enumerate(pieces):
                packet = dpkt.ip.IP(
                    src=source,
                    dst=destination,
                    p=protocol,
                    ttl=64,
                    id=identifier,
                    data=body,
                )
                packet.offset = offset // 8
                packet.mf = more
                if (args.profile == "options" and offset == 0) or (
                    args.profile == "header_conflict" and index == 1
                ):
                    packet.opts = b"\x01" * 4
                    packet.hl = 6
                packet.len = len(packet)
                sender.sendto(bytes(packet), ("10.77.0.1", 0))
        logger.info(
            "真实 IPv4 分片已发送：%s %s，共 %s 片",
            args.transport,
            args.profile,
            len(pieces),
        )
    except Exception:
        logger.exception("IPv4 分片虚拟网发送失败")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
