#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""wsclient.py — 零依赖 WebSocket 客户端(RFC6455, 纯标准库)。

统一给两个平台用:
  - 抖音:  ws://127.0.0.1:1089/ws/<rid>   (本地 douyinLive 服务, 文本帧 JSON)
  - 哔哩哔哩: wss://broadcastlv...:443/sub  (官方弹幕 WS, 二进制帧)

只实现客户端需要的最小集合: 握手 / 掩码发送 / 收帧(自动处理 ping→pong、分片)。
"""

import base64
import os
import socket
import ssl
import struct
import threading

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BIN = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WSClosed(Exception):
    """连接已被对端(或本地)关闭。"""


class WSClient:
    def __init__(self, url: str, extra_headers: dict | None = None, name: str = "ws"):
        self.url = url
        self.name = name
        self.extra_headers = extra_headers or {}
        self.sock: socket.socket | None = None
        self._lock = threading.Lock()

        u = url
        if u.startswith("wss://"):
            self.secure = True
            rest = u[6:]
            default_port = 443
        elif u.startswith("ws://"):
            self.secure = False
            rest = u[5:]
            default_port = 80
        else:
            raise ValueError(f"不支持的 URL: {url}")
        path_start = rest.find("/")
        if path_start < 0:
            hostport, self.path = rest, "/"
        else:
            hostport, self.path = rest[:path_start], rest[path_start:]
        if "@" in hostport:
            hostport = hostport.rsplit("@", 1)[1]
        if hostport.startswith("["):  # IPv6
            host, _, port_s = hostport[1:].partition("]")
            port_s = port_s.lstrip(":")
        else:
            host, _, port_s = hostport.partition(":")
        self.host = host
        self.port = int(port_s) if port_s else default_port

    # ────────────────────────── 连接 ──────────────────────────

    def connect(self, timeout: float = 15.0):
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        if self.secure:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=self.host)
        sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            f"GET {self.path} HTTP/1.1",
            f"Host: {self.host}:{self.port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            f"User-Agent: {self.extra_headers.get('User-Agent', 'Mozilla/5.0')} ",
        ]
        for k, v in self.extra_headers.items():
            if k != "User-Agent":
                lines.append(f"{k}: {v}")
        req = "\r\n".join(lines) + "\r\n\r\n"
        sock.sendall(req.encode("utf-8"))
        # 逐字节读响应头, 防止多吃到帧数据
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(1)
            if not chunk:
                raise WSClosed(f"{self.name}: 握手时连接被关闭")
            buf += chunk
            if len(buf) > 65536:
                raise WSClosed(f"{self.name}: 握手响应异常")
        head = buf.split(b"\r\n\r\n", 1)[0].decode("utf-8", "replace")
        status = head.split("\r\n", 1)[0]
        if " 101 " not in status:
            raise WSClosed(f"{self.name}: 握手被拒绝: {status}")
        self.sock = sock
        return self

    # ────────────────────────── 发送 ──────────────────────────

    def _send_frame(self, opcode: int, payload: bytes):
        sock = self.sock
        if sock is None:
            raise WSClosed(f"{self.name}: 未连接")
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        header = bytearray()
        header.append(0x80 | opcode)  # FIN=1
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        with self._lock:
            sock.sendall(bytes(header) + masked)

    def send_text(self, text: str):
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes):
        self._send_frame(OP_BIN, data)

    # ────────────────────────── 接收 ──────────────────────────

    def _read_exact(self, n: int) -> bytes:
        sock = self.sock
        if sock is None:
            raise WSClosed(f"{self.name}: 未连接")
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise WSClosed(f"{self.name}: 连接被关闭")
            buf += chunk
        return buf

    def recv_message(self, timeout: float | None = None) -> tuple[int, bytes]:
        """收一条完整消息(自动拼分片)。返回 (opcode, payload)。

        ping 自动回 pong 不上抛; close 上抛 WSClosed。
        timeout 为 None 时用当前 socket 超时。
        """
        if self.sock is not None and timeout is not None:
            self.sock.settimeout(timeout)
        buffer = b""
        final_op = None
        while True:
            b1, b2 = self._read_exact(2)
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            ln = b2 & 0x7F
            if ln == 126:
                (ln,) = struct.unpack(">H", self._read_exact(2))
            elif ln == 127:
                (ln,) = struct.unpack(">Q", self._read_exact(8))
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(ln) if ln else b""
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if opcode == OP_CLOSE:
                try:
                    self._send_frame(OP_CLOSE, payload[:2])
                except Exception:
                    pass
                raise WSClosed(f"{self.name}: 服务端发送了 CLOSE")
            if opcode == OP_CONT:
                if final_op is None:
                    continue  # 异常分片, 忽略
                buffer += payload
            else:
                final_op = opcode
                buffer = payload
            if fin and final_op is not None:
                return final_op, buffer

    # ────────────────────────── 关闭 ──────────────────────────

    def close(self):
        sock = self.sock
        self.sock = None
        if sock is not None:
            try:
                with self._lock:
                    sock.sendall(b"\x88\x80" + os.urandom(4))  # CLOSE 帧(掩码长度位)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
