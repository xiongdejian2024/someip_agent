"""在自有 netns 的 veth 注入真实选项字节；AF_PACKET 避免宿主 IP 栈改写或先行拒绝。"""

import argparse
import logging
import socket

import dpkt
from ipv4_option_vectors import OPTION_CASES

logger = logging.getLogger(__name__)
GOLDEN = bytes.fromhex("456780030000000c001100170101020041280000")


def main():
    parser = argparse.ArgumentParser(description="IPv4 选项及分片虚拟网发送器")
    parser.add_argument("transport", choices=["udp", "tcp"])
    parser.add_argument("profile", choices=OPTION_CASES)
    parser.add_argument("--unfragmented", action="store_true")
    args = parser.parse_args()
    index = list(OPTION_CASES).index(args.profile)
    options, _valid = OPTION_CASES[args.profile]
    port = 41060 + index + (20 if args.unfragmented else 0)
    source, destination = socket.inet_aton("10.77.0.2"), socket.inet_aton("10.77.0.1")
    if args.transport == "udp":
        transport = dpkt.udp.UDP(sport=port, dport=30618, data=GOLDEN)
        transport.ulen = len(transport)
        protocol, split, base = 17, 16, 0x7810
    else:
        transport = dpkt.tcp.TCP(
            sport=port,
            dport=30618,
            seq=101,
            ack=701,
            flags=dpkt.tcp.TH_ACK | dpkt.tcp.TH_PUSH,
            data=GOLDEN,
        )
        protocol, split, base = 6, 24, 0x7830
    full = dpkt.ip.IP(src=source, dst=destination, p=protocol, ttl=64, data=transport)
    full.len = len(full)
    payload = bytes(full)[20:]
    pieces = (
        [(0, payload, False)]
        if args.unfragmented
        else [
            (split, payload[split:], False),
            (0, payload[:split], True),
        ]
    )
    identifier = base + index + 1 + (0x100 if args.unfragmented else 0)
    try:
        with socket.socket(
            socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800)
        ) as sender:
            sender.bind(("eth0", 0))
            mac = sender.getsockname()[4]
            for offset, body, more in pieces:
                packet = dpkt.ip.IP(
                    src=source,
                    dst=destination,
                    p=protocol,
                    ttl=64,
                    id=identifier,
                    data=body,
                )
                packet.offset, packet.mf = offset // 8, more
                if offset == 0:
                    packet.opts = options
                    packet.hl = 5 + len(options) // 4
                packet.len = len(packet)
                frame = dpkt.ethernet.Ethernet(
                    src=mac,
                    dst=bytes.fromhex("ffffffffffff"),
                    type=0x0800,
                    data=bytes(packet),
                )
                sender.send(bytes(frame))
        logger.info(
            "真实 IPv4 选项已发送：%s %s 分片=%s",
            args.transport,
            args.profile,
            not args.unfragmented,
        )
    except Exception:
        logger.exception("IPv4 选项虚拟网发送失败")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
