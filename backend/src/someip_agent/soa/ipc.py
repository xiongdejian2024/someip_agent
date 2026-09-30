from __future__ import annotations

import asyncio
import codecs
import json
import socket
from collections.abc import Iterator
from typing import Any

MAX_FRAME = 4 * 1024 * 1024


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    """异步监控通道复用同一长度帧契约与资源限制。"""
    header = await reader.readexactly(8)
    if any(ch not in b"0123456789abcdefABCDEF" for ch in header):
        raise ValueError("原生监控帧长度头非法")
    size = int(header, 16)
    if not 0 < size <= MAX_FRAME:
        raise ValueError("原生监控帧大小超限")
    message = json.loads(await reader.readexactly(size))
    if not isinstance(message, dict):
        raise ValueError("原生监控消息必须为对象")
    return message


def send_data_to(conn: socket.socket, data: dict[str, Any]) -> None:
    """保持 SAT 控制通道 8 位 ASCII 十六进制长度头；长度按 UTF-8 字节计算。"""
    body = json.dumps(data, ensure_ascii=True).encode("utf-8")
    if not 0 < len(body) <= MAX_FRAME:
        raise ValueError("控制消息大小超限")
    conn.sendall(f"{len(body):08x}".encode("ascii") + body)


def recv_exact(conn: socket.socket, size: int) -> bytes:
    parts = bytearray()
    while len(parts) < size:
        data = conn.recv(size - len(parts))
        if not data:
            raise EOFError("原生控制连接已关闭")
        parts.extend(data)
    return bytes(parts)


def recv_data_from(conn: socket.socket) -> bytes:
    header = recv_exact(conn, 8)
    if any(ch not in b"0123456789abcdefABCDEF" for ch in header):
        raise ValueError("控制消息长度头非法")
    size = int(header, 16)
    if not 0 < size <= MAX_FRAME:
        raise ValueError("控制消息大小超限")
    return recv_exact(conn, size)


def member_messages(conn: socket.socket) -> Iterator[dict[str, Any]]:
    """成员通道是 JSON 文档流；正确处理拆包、粘包、字符串中的 }{ 与多字节字符。"""
    utf8 = codecs.getincrementaldecoder("utf-8")()
    decoder = json.JSONDecoder()
    buffered = ""
    while True:
        chunk = conn.recv(8192)
        if not chunk:
            if buffered.strip():
                raise EOFError("原生成员连接在 JSON 消息中途关闭")
            return
        buffered += utf8.decode(chunk)
        if len(buffered.encode("utf-8")) > MAX_FRAME:
            raise ValueError("成员消息大小超限")
        while buffered.strip():
            buffered = buffered.lstrip()
            try:
                message, end = decoder.raw_decode(buffered)
            except json.JSONDecodeError:
                break
            if not isinstance(message, dict):
                raise ValueError("成员消息必须为 JSON 对象")
            buffered = buffered[end:]
            yield message
