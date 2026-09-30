"""虚拟网另一节点的真实 socket 发送器；不会模拟原生收包结果。"""

import argparse
import logging
import socket
import time

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="原生监听虚拟网测试发送器")
    parser.add_argument("transport", choices=["udp", "tcp"])
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("payload_hex")
    parser.add_argument("--interface", default="10.77.0.2")
    parser.add_argument("--expect-reply", help="独立服务响应的黄金字节")
    args = parser.parse_args()
    body = bytes.fromhex(args.payload_hex)
    kind = socket.SOCK_DGRAM if args.transport == "udp" else socket.SOCK_STREAM
    family = socket.AF_INET6 if ":" in args.host else socket.AF_INET
    try:
        with socket.socket(family, kind) as sender:
            sender.settimeout(3)
            sender.bind((args.interface, 0))
            if args.transport == "tcp":
                sender.settimeout(3)
                sender.connect((args.host, args.port))
                for part in (body[:5], body[5:17], body[17:]):
                    sender.sendall(part)
                    time.sleep(0.02)
            else:
                if args.host.startswith("239."):
                    sender.setsockopt(
                        socket.IPPROTO_IP,
                        socket.IP_MULTICAST_IF,
                        socket.inet_aton(args.interface),
                    )
                    sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                sender.sendto(body, (args.host, args.port))
            if args.expect_reply:
                expected = bytes.fromhex(args.expect_reply)
                reply = bytearray()
                while len(reply) < len(expected):
                    chunk = sender.recv(4096)
                    if not chunk:
                        raise EOFError("独立服务响应中途关闭")
                    reply.extend(chunk)
                if bytes(reply) != expected:
                    raise AssertionError("独立服务响应与黄金字节不符")
        logger.info("虚拟网报文已发送")
    except Exception:
        logger.exception("虚拟网报文发送失败")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
