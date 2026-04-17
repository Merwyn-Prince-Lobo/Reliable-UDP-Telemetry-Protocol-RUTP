"""
server.py — Reliable UDP Telemetry Server
Receives telemetry packets, sends ACK/NACK, logs received data.
"""

from threaded_server import ThreadedServerWrapper
import socket
import argparse
import logging
import time
import random
import json
from protocol import (
    parse_packet, build_ack, build_nack,
    PKT_DATA, PKT_HELLO, PKT_BYE,
    MAX_PACKET_SIZE
)

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SERVER] %(levelname)s %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Session class
# ─────────────────────────────────────────────
class TelemetrySession:
    def __init__(self, addr):
        self.addr = addr
        self.expected_seq = 0
        self.received = 0
        self.duplicates = 0
        self.out_of_order = 0
        self.start_time = time.time()
        self.buffer = {}

    def stats(self):
        elapsed = time.time() - self.start_time
        return {
            "client": str(self.addr),
            "received": self.received,
            "duplicates": self.duplicates,
            "out_of_order": self.out_of_order,
            "duration_s": round(elapsed, 2),
        }


# ─────────────────────────────────────────────
# Server
# ─────────────────────────────────────────────
class ReliableUDPServer:
    def __init__(self, host, port, loss_sim=0.0):
        self.host = host
        self.port = port
        self.loss_sim = loss_sim

        self.sessions = {}

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))

        log.info(f"Listening on {host}:{port} (ACK loss={loss_sim*100:.0f}%)")

    # ─────────────────────────────
    # Session
    # ─────────────────────────────
    def _get_session(self, addr):
        if addr not in self.sessions:
            self.sessions[addr] = TelemetrySession(addr)
            log.info(f"New session from {addr}")
        return self.sessions[addr]

    # ─────────────────────────────
    # ACK / NACK
    # ─────────────────────────────
    def _send_ack(self, addr, msg_id, seq):
        if self.loss_sim > 0 and random.random() < self.loss_sim:
            return
        pkt = build_ack(msg_id, seq)
        self.sock.sendto(pkt, addr)

    def _send_nack(self, addr, msg_id, seq):
        pkt = build_nack(msg_id, seq)
        self.sock.sendto(pkt, addr)

    # ─────────────────────────────
    # Handlers
    # ─────────────────────────────
    def _handle_hello(self, addr, msg_id, seq):
        sess = self._get_session(addr)
        sess.expected_seq = 0
        log.info(f"HELLO from {addr}")
        self._send_ack(addr, msg_id, seq)

    def _handle_bye(self, addr, msg_id, seq):
        sess = self._get_session(addr)
        log.info(f"BYE from {addr} — stats: {json.dumps(sess.stats())}")
        self._send_ack(addr, msg_id, seq)
        del self.sessions[addr]

    def _handle_data(self, addr, msg_id, seq, payload):
        sess = self._get_session(addr)

        if seq < sess.expected_seq:
            sess.duplicates += 1
            self._send_ack(addr, msg_id, seq)
            return

        if seq == sess.expected_seq:
            self._deliver(addr, seq, payload, sess)
            sess.expected_seq += 1

            while sess.expected_seq in sess.buffer:
                buffered = sess.buffer.pop(sess.expected_seq)
                self._deliver(addr, sess.expected_seq, buffered, sess)
                sess.expected_seq += 1
        else:
            sess.out_of_order += 1
            sess.buffer[seq] = payload
            self._send_nack(addr, msg_id, sess.expected_seq)
            return

        self._send_ack(addr, msg_id, seq)

    def _deliver(self, addr, seq, payload, sess):
        sess.received += 1
        try:
            data = json.loads(payload.decode())
            log.info(f"DATA seq={seq:04d} from {addr}: {data}")
        except:
            log.info(f"DATA seq={seq:04d} raw: {payload.hex()}")

    # ─────────────────────────────
    # 🔥 FIX: REQUIRED FOR THREADING
    # ─────────────────────────────
    def _process_packet(self, raw, addr):
        try:
            msg_id, pkt_type, flags, seq, payload = parse_packet(raw)
        except ValueError as e:
            log.warning(f"Bad packet from {addr}: {e}")
            return

        if pkt_type == PKT_HELLO:
            self._handle_hello(addr, msg_id, seq)

        elif pkt_type == PKT_DATA:
            self._handle_data(addr, msg_id, seq, payload)

        elif pkt_type == PKT_BYE:
            self._handle_bye(addr, msg_id, seq)

        else:
            log.warning(f"Unknown packet type {pkt_type}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=9000)
    parser.add_argument('--loss-sim', type=float, default=0.0)
    args = parser.parse_args()

    server = ReliableUDPServer(args.host, args.port, args.loss_sim)
    ThreadedServerWrapper(server).run()


if __name__ == '__main__':
    main()
