"""Local HTTP Bridge Server for Chrome Extension & Chess Clients.
Connects browser extension to Leela Chess Zero (Lc0) on RTX 3070 with
Adjustable Strength / Elo Level (1 to 7).
"""

import json
import math
import os
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import chess

from play_chess import Lc0Engine, HybridChessEngine, LC0_DIR, parse_uci_info

PORT = 5000
engine: Lc0Engine = None

# Strength presets mapping levels (1 to 8) to (nodes, temperature, move_time, hybrid)
STRENGTH_LEVELS = {
    1: {"name": "Beginner (~1000 ELO)", "nodes": 10, "temp": 2.2, "time": 0.2, "hybrid": False},
    2: {"name": "Casual (~1400 ELO)", "nodes": 40, "temp": 1.6, "time": 0.4, "hybrid": False},
    3: {"name": "Intermediate (~1800 ELO)", "nodes": 150, "temp": 1.3, "time": 0.6, "hybrid": False},
    4: {"name": "Advanced (~2200 ELO)", "nodes": 500, "temp": 1.0, "time": 1.0, "hybrid": False},
    5: {"name": "Master (~2600 ELO)", "nodes": 1200, "temp": 1.0, "time": 1.5, "hybrid": False},
    6: {"name": "Super-GM (~3100 ELO)", "nodes": 2000, "temp": 1.0, "time": 2.0, "hybrid": False},
    7: {"name": "Max AlphaZero (3500+ ELO)", "nodes": 3500, "temp": 1.0, "time": 2.5, "hybrid": True},
    8: {"name": " Centaur God (Lc0 GPU + SF19 CPU 3800+ ELO)", "nodes": 0, "temp": 0.75, "time": 3.0, "hybrid": True},
}


def _reject_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


class BridgeHandler(BaseHTTPRequestHandler):
    timeout = 5.0

    def _read_body(self, content_length):
        deadline = time.monotonic() + self.timeout
        body = bytearray()
        while len(body) < content_length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Request body read timed out")
            self.connection.settimeout(remaining)
            chunk = self.rfile.read1(min(65536, content_length - len(body)))
            if not chunk:
                raise ValueError("Incomplete request body")
            body.extend(chunk)
        self.connection.settimeout(self.timeout)
        return body

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "status": "ready",
                "engine": "Hybrid Centaur (Lc0 BT3 GPU + Stockfish 19 CPU)",
                "gpu": "NVIDIA GeForce RTX 3070",
                "backend": "cuda-fp16 + avxvnni",
                "levels": {k: v["name"] for k, v in STRENGTH_LEVELS.items()},
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def do_POST(self):
        if self.path != "/move":
            self._send_json(404, {"error": "Not found"})
            return

        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("A single Content-Length is required")
            length = lengths[0].strip()
            if not length or not length.isascii() or not length.isdecimal():
                raise ValueError("Invalid Content-Length")
            content_length = int(length)
            if not 0 < content_length <= 1024 * 1024:
                raise ValueError("Content-Length must be between 1 and 1048576 bytes")
            body = self._read_body(content_length)
            data = json.loads(body.decode("utf-8"), parse_constant=_reject_json_constant)
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")

            level = data.get("level", 7)
            if type(level) is not int or level not in STRENGTH_LEVELS:
                raise ValueError("level must be an integer from 1 to 8")
            preset = STRENGTH_LEVELS[level]
            nodes = data.get("nodes", preset["nodes"])
            if type(nodes) is not int or nodes < 0:
                raise ValueError("nodes must be a nonnegative integer")
            temperature = data.get("temperature", preset["temp"])
            move_time = data.get("move_time", preset["time"])
            for name, value in (("temperature", temperature), ("move_time", move_time)):
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise ValueError(f"{name} must be a finite positive number")

            fen = data.get("fen", "")
            moves_uci = data.get("moves", [])
            multipv = data.get("multipv", 1)
            if type(multipv) is not int or not 1 <= multipv <= 8:
                raise ValueError("multipv must be an integer from 1 to 8")
            if not isinstance(fen, str):
                raise ValueError("fen must be a string")
            if not isinstance(moves_uci, list) or any(not isinstance(move, str) for move in moves_uci):
                raise ValueError("moves must be a list of UCI strings")
            if any(character in fen for character in "\r\n"):
                raise ValueError("Invalid FEN")
            board = chess.Board(fen) if fen else chess.Board()
            if not board.is_valid():
                raise ValueError("Invalid FEN position")
            starting_fen = board.fen() if fen else None
            normalized_moves = []
            for move_uci in moves_uci:
                move = board.parse_uci(move_uci)
                if move not in board.legal_moves:
                    raise ValueError(f"Illegal move: {move_uci}")
                normalized_moves.append(move.uci())
                board.push(move)
            moves_uci = normalized_moves
        except TimeoutError:
            self.close_connection = True
            self.connection.settimeout(self.timeout)
            try:
                self._send_json(408, {"error": "Request body read timed out"})
            except OSError:
                pass
            return
        except OSError:
            self.close_connection = True
            return
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
            self.close_connection = True
            try:
                import select
                while select.select([self.connection], [], [], 0.05)[0]:
                    chunk = self.connection.recv(65536)
                    if not chunk:
                        break
            except Exception:
                pass
            self._send_json(400, {"error": str(error)})
            return

        if board.is_game_over():
            self._send_json(400, {"error": "Position is terminal", "result": board.result()})
            return

        try:
            engine._send(f"setoption name PolicyTemperature value {temperature}")
            if isinstance(engine, HybridChessEngine):
                best_move, info_line, candidates = engine.analyse(
                    moves_uci,
                    move_time_sec=move_time,
                    nodes=nodes or None,
                    fen=starting_fen,
                    use_hybrid=preset.get("hybrid", level >= 7),
                    multipv=multipv,
                )
            else:
                best_move, info_line, candidates = engine.analyse(
                    moves_uci, move_time_sec=move_time, nodes=nodes or None, fen=starting_fen, multipv=multipv
                )
            try:
                move = board.parse_uci(best_move)
                if move not in board.legal_moves:
                    raise ValueError("Illegal engine move")
                san_move = board.san(move)
                best_move = move.uci()
            except (ValueError, TypeError, AttributeError):
                self._send_json(502, {"error": "Engine returned an invalid bestmove"})
                return

            telemetry = parse_uci_info(info_line)
            legal_candidates = []
            seen_moves = set()
            for candidate in candidates or []:
                if not isinstance(candidate, dict):
                    continue
                try:
                    candidate_move = board.parse_uci(candidate.get("move", ""))
                    if candidate_move not in board.legal_moves:
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
                canonical = candidate_move.uci()
                if canonical not in seen_moves:
                    legal_candidates.append({**candidate, "move": canonical})
                    seen_moves.add(canonical)
            pv_moves = telemetry.get("pv") or []
            ponder_move = pv_moves[1] if len(pv_moves) >= 2 else None

            self._send_json(200, {
                "bestmove": best_move,
                "in_check": board.is_check(),
                "legal_move_count": board.legal_moves.count(),
                "from": best_move[:2],
                "to": best_move[2:4],
                "promotion": best_move[4] if len(best_move) > 4 else None,
                "san": san_move,
                "ponder": ponder_move,
                "pv": pv_moves[:6],
                "wdl": telemetry.get("wdl"),
                "score_cp": telemetry.get("score_cp"),
                "mate": telemetry.get("mate"),
                "depth": telemetry.get("depth"),
                "nodes": telemetry.get("nodes"),
                "nps": telemetry.get("nps"),
                "candidates": legal_candidates or [{"move": best_move, "score_cp": telemetry.get("score_cp"), "mate": telemetry.get("mate")}],
                "level": level,
                "level_name": preset["name"],
                "info": info_line,
            })
        except Exception as error:
            print(f"Engine search error: {error}")
            self._send_json(500, {"error": "Engine search failed"})

    def log_message(self, format, *args):
        pass


def main():
    global engine
    host = os.environ.get("BRIDGE_HOST", "0.0.0.0")
    print("=" * 60)
    print("  🚀 AlphaZero (Lc0) Bridge Server with Adjustable Strength")
    print(f"  Listening on http://{host}:{PORT} (Local & Network)")
    print("  Difficulty Levels: 1 (Beginner ~1000) to 8 (Centaur God 3800+)")
    print("=" * 60)

    nets = [f for f in os.listdir(LC0_DIR) if f.endswith(".pb.gz") or f.endswith(".pb")]
    chosen_net = None
    if "BT3-768x15x24h-swa-2790000.pb.gz" in nets:
        chosen_net = os.path.join(LC0_DIR, "BT3-768x15x24h-swa-2790000.pb.gz")
        print("[*] Loaded Net: BT3-768x15x24h (Super-GM Model)")
    elif nets:
        chosen_net = os.path.join(LC0_DIR, nets[0])
        print(f"[*] Loaded Net: {nets[0]}")

    print("[*] Initializing Hybrid Centaur Engine (Lc0 RTX 3070 CUDA + Stockfish 19 CPU)...")
    engine = HybridChessEngine(chosen_net)
    print("[*] Engine ready! Awaiting moves from Extension or Client...\n")

    host = os.environ.get("BRIDGE_HOST", "0.0.0.0")
    server = HTTPServer((host, PORT), BridgeHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        if engine:
            engine.close()
        server.server_close()


if __name__ == "__main__":
    main()
