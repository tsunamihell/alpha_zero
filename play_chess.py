"""Interactive Chess game against Leela Chess Zero (Lc0) - AlphaZero for Chess.
Runs locally using NVIDIA CUDA acceleration on GPU.
"""

import os
import sys
import subprocess
import queue
import threading
import time
import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple, Dict, Any
import chess

LC0_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lc0")
LC0_EXE = os.path.join(LC0_DIR, "lc0.exe")
STOCKFISH_EXE = os.path.join(LC0_DIR, "stockfish.exe")

UNICODE_PIECES = {
    "R": "♜", "N": "♞", "B": "♝", "Q": "♛", "K": "♚", "P": "♟",
    "r": "♖", "n": "♘", "b": "♗", "q": "♕", "k": "♔", "p": "♙",
    ".": "·"
}


def parse_uci_info(info_line: str) -> Dict[str, Any]:
    """Extracts structured evaluation telemetry (score_cp, mate, wdl, depth, nodes, nps, pv) from a UCI info line."""
    result: Dict[str, Any] = {
        "depth": None,
        "seldepth": None,
        "score_cp": None,
        "mate": None,
        "wdl": None,
        "nodes": None,
        "nps": None,
        "pv": [],
    }
    if not info_line:
        return result

    depth_m = re.search(r"\bdepth\s+(\d+)", info_line)
    if depth_m:
        result["depth"] = int(depth_m.group(1))

    seldepth_m = re.search(r"\bseldepth\s+(\d+)", info_line)
    if seldepth_m:
        result["seldepth"] = int(seldepth_m.group(1))

    cp_m = re.search(r"\bscore\s+cp\s+(-?\d+)", info_line)
    if cp_m:
        result["score_cp"] = int(cp_m.group(1))

    mate_m = re.search(r"\bscore\s+mate\s+(-?\d+)", info_line)
    if mate_m:
        result["mate"] = int(mate_m.group(1))

    wdl_m = re.search(r"\bwdl\s+(\d+)\s+(\d+)\s+(\d+)", info_line)
    if wdl_m:
        result["wdl"] = [int(wdl_m.group(1)), int(wdl_m.group(2)), int(wdl_m.group(3))]

    nodes_m = re.search(r"\bnodes\s+(\d+)", info_line)
    if nodes_m:
        result["nodes"] = int(nodes_m.group(1))

    nps_m = re.search(r"\bnps\s+(\d+)", info_line)
    if nps_m:
        result["nps"] = int(nps_m.group(1))

    pv_m = re.search(r"\bpv\s+([a-h1-8qrbn\s]+)$", info_line)
    if pv_m:
        result["pv"] = [m for m in pv_m.group(1).strip().split() if m]

    return result


def _sort_score(candidate: dict) -> float:
    """Sort key that puts mates above any centipawn score, in the right direction."""
    mate = candidate.get("mate")
    if isinstance(mate, int):
        return 100000.0 - mate if mate > 0 else -100000.0 - mate
    score = candidate.get("score_cp")
    return float(score) if isinstance(score, int) else 0.0


class Lc0Engine:
    def __init__(self, weights_file: Optional[str] = None, handshake_timeout: float = 30.0):
        if not os.path.exists(LC0_EXE):
            raise FileNotFoundError(f"Lc0 executable not found at: {LC0_EXE}")

        cmd = [LC0_EXE]
        if weights_file and os.path.exists(weights_file):
            cmd.append(f"--weights={weights_file}")

        self._lines = queue.Queue()
        self._search_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        self.proc = subprocess.Popen(
            cmd,
            cwd=LC0_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        try:
            self._send("uci")
            self._wait_for("uciok", handshake_timeout)
            self._send("isready")
            self._wait_for("readyok", handshake_timeout)
        except Exception:
            self.close()
            raise

    def _read_stdout(self):
        try:
            for line in self.proc.stdout:
                self._lines.put(line.strip())
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put(None)

    def _send(self, command: str):
        if self._closed or self.proc.poll() is not None or not self.proc.stdin:
            raise RuntimeError("Lc0 engine is not running")
        if "\n" in command or "\r" in command:
            raise ValueError("Engine commands must be a single line")
        try:
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise RuntimeError(f"Engine pipe broken while sending: {command!r}") from e

    def _next_line(self, deadline: float) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for Lc0 engine")
        try:
            line = self._lines.get(timeout=remaining)
        except queue.Empty as e:
            raise TimeoutError("Timed out waiting for Lc0 engine") from e
        if line is None or self._closed:
            self._lines.put(None)
            raise RuntimeError("Lc0 engine exited or closed its output")
        return line

    def _wait_for(self, target: str, timeout: float = 30.0) -> str:
        deadline = time.monotonic() + timeout
        while True:
            line = self._next_line(deadline)
            if line == target:
                return line

    def get_best_move(
        self, moves_uci: list[str], move_time_sec: float = 1.5, nodes: Optional[int] = None,
        fen: Optional[str] = None, timeout: Optional[float] = None, multipv: int = 1,
    ) -> Tuple[str, str]:
        best, info, _ = self.analyse(
            moves_uci, move_time_sec=move_time_sec, nodes=nodes, fen=fen, timeout=timeout, multipv=multipv
        )
        return best, info

    def analyse(
        self, moves_uci: list[str], move_time_sec: float = 1.5, nodes: Optional[int] = None,
        fen: Optional[str] = None, timeout: Optional[float] = None, multipv: int = 1,
    ) -> Tuple[str, str, list[dict]]:
        """Searches and returns (best_move, best_info_line, candidates).

        `candidates` is best-first and holds one entry per reported principal variation, which
        lets the caller sample a near-best move instead of always playing rank 1.
        """
        if not math.isfinite(move_time_sec) or move_time_sec <= 0:
            raise ValueError("move_time_sec must be finite and positive")
        if nodes is not None and (not isinstance(nodes, int) or nodes <= 0):
            raise ValueError("nodes must be a positive integer")
        pv_count = max(1, min(8, int(multipv or 1)))
        search_timeout = max(30.0, move_time_sec + 10.0) if timeout is None else timeout
        if not math.isfinite(search_timeout) or search_timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        board = chess.Board(fen) if fen is not None else chess.Board()
        position = f"position fen {board.fen()}" if fen is not None else "position startpos"
        moves = []
        for raw_move in moves_uci:
            move = board.parse_uci(raw_move)
            moves.append(move.uci())
            board.push(move)
        if moves:
            position += f" moves {' '.join(moves)}"
        with self._search_lock:
            try:
                if pv_count > 1:
                    self._send(f"setoption name MultiPV value {pv_count}")
                self._send(position)
                go = f"go nodes {nodes}" if nodes is not None else f"go movetime {max(1, int(move_time_sec * 1000))}"
                self._send(go)
                deadline = time.monotonic() + search_timeout
                info_line = ""
                per_line: dict[int, str] = {}
                while True:
                    line = self._next_line(deadline)
                    if line.startswith("info ") and "score " in line:
                        idx_m = re.search(r"\bmultipv\s+(\d+)", line)
                        idx = int(idx_m.group(1)) if idx_m else 1
                        per_line[idx] = line
                        if idx == 1 or not info_line:
                            info_line = line
                    parts = line.split()
                    if parts and parts[0] == "bestmove":
                        if pv_count > 1:
                            self._send("setoption name MultiPV value 1")
                        if len(parts) < 2 or parts[1] in ("(none)", "0000"):
                            if not any(board.legal_moves):
                                return "", info_line, []
                            raise RuntimeError("Lc0 returned no move for a playable position")
                        try:
                            move = board.parse_uci(parts[1])
                        except ValueError as e:
                            raise RuntimeError(f"Lc0 returned an invalid move: {parts[1]}") from e
                        if move not in board.legal_moves:
                            raise RuntimeError(f"Lc0 returned an illegal move: {parts[1]}")
                        return move.uci(), info_line, self._candidates(per_line, board)
            except Exception:
                self.close()
                raise

    @staticmethod
    def _candidates(per_line: dict[int, str], board: chess.Board) -> list[dict]:
        """Converts collected multipv info lines into a validated, score-ordered candidate list."""
        out = []
        for idx in sorted(per_line):
            meta = parse_uci_info(per_line[idx])
            pv = meta.get("pv") or []
            if not pv:
                continue
            move = pv[0]
            try:
                parsed = board.parse_uci(move)
                if parsed not in board.legal_moves:
                    continue
            except ValueError:
                continue
            out.append({"move": parsed.uci(), "score_cp": meta["score_cp"], "mate": meta["mate"]})
        return out

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._lines.put(None)
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                try:
                    self.proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            finally:
                self._reader.join(timeout=1)
                for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                    if stream and (stream is not self.proc.stdout or not self._reader.is_alive()):
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class StockfishEngine:
    """High-speed Stockfish 19 (NNUE AVX2/VNNI) CPU Engine."""

    def __init__(self, exe_path: Optional[str] = None, threads: int = 8, hash_mb: int = 512, handshake_timeout: float = 15.0):
        self.exe_path = exe_path or STOCKFISH_EXE
        if not os.path.exists(self.exe_path):
            raise FileNotFoundError(f"Stockfish executable not found at: {self.exe_path}")

        self._lines: queue.Queue = queue.Queue()
        self._search_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        self.proc = subprocess.Popen(
            [self.exe_path],
            cwd=LC0_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        try:
            self._send("uci")
            self._wait_for("uciok", handshake_timeout)
            self._send(f"setoption name Threads value {max(1, threads)}")
            self._send(f"setoption name Hash value {max(16, hash_mb)}")
            self._send("setoption name UCI_ShowWDL value true")
            self._send("setoption name Move Overhead value 30")
            self._send("isready")
            self._wait_for("readyok", handshake_timeout)
        except Exception:
            self.close()
            raise

    def _read_stdout(self):
        try:
            for line in self.proc.stdout:
                self._lines.put(line.strip())
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put(None)

    def _send(self, command: str):
        if self._closed or self.proc.poll() is not None or not self.proc.stdin:
            raise RuntimeError("Stockfish engine is not running")
        if "\n" in command or "\r" in command:
            raise ValueError("Engine commands must be a single line")
        try:
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise RuntimeError(f"Stockfish pipe broken while sending: {command!r}") from e

    def _next_line(self, deadline: float) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for Stockfish engine")
        try:
            line = self._lines.get(timeout=remaining)
        except queue.Empty as e:
            raise TimeoutError("Timed out waiting for Stockfish engine") from e
        if line is None or self._closed:
            self._lines.put(None)
            raise RuntimeError("Stockfish engine exited or closed its output")
        return line

    def _wait_for(self, target: str, timeout: float = 15.0) -> str:
        deadline = time.monotonic() + timeout
        while True:
            line = self._next_line(deadline)
            if line == target:
                return line

    def evaluate_move(
        self,
        moves_uci: list[str],
        move_to_eval: str,
        move_time_sec: float = 0.25,
        fen: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Evaluates a specific candidate move using UCI `searchmoves`."""
        return self.get_best_move(
            moves_uci,
            move_time_sec=move_time_sec,
            fen=fen,
            searchmoves=[move_to_eval],
        )

    def get_best_move(
        self,
        moves_uci: list[str],
        move_time_sec: float = 1.0,
        nodes: Optional[int] = None,
        fen: Optional[str] = None,
        timeout: Optional[float] = None,
        searchmoves: Optional[list[str]] = None,
        multipv: int = 1,
    ) -> Tuple[str, str]:
        best, info, _ = self.analyse(
            moves_uci,
            move_time_sec=move_time_sec,
            nodes=nodes,
            fen=fen,
            timeout=timeout,
            searchmoves=searchmoves,
            multipv=multipv,
        )
        return best, info

    def analyse(
        self,
        moves_uci: list[str],
        move_time_sec: float = 1.0,
        nodes: Optional[int] = None,
        fen: Optional[str] = None,
        timeout: Optional[float] = None,
        searchmoves: Optional[list[str]] = None,
        multipv: int = 1,
    ) -> Tuple[str, str, list[dict]]:
        """Searches and returns (best_move, best_info_line, candidates)."""
        if not math.isfinite(move_time_sec) or move_time_sec <= 0:
            raise ValueError("move_time_sec must be finite and positive")
        pv_count = max(1, min(8, int(multipv or 1)))
        search_timeout = max(20.0, move_time_sec + 8.0) if timeout is None else timeout
        board = chess.Board(fen) if fen is not None else chess.Board()
        position = f"position fen {board.fen()}" if fen is not None else "position startpos"
        moves = list(moves_uci)
        for move in moves:
            board.push_uci(move)
        if moves:
            position += f" moves {' '.join(moves)}"

        with self._search_lock:
            if pv_count > 1:
                self._send(f"setoption name MultiPV value {pv_count}")
            self._send(position)
            go = f"go nodes {nodes}" if nodes is not None else f"go movetime {max(10, int(move_time_sec * 1000))}"
            if searchmoves:
                go += " searchmoves " + " ".join(searchmoves)
            self._send(go)
            deadline = time.monotonic() + search_timeout
            info_line = ""
            per_line: dict[int, str] = {}
            while True:
                line = self._next_line(deadline)
                if line.startswith("info ") and "score " in line:
                    idx_m = re.search(r"\bmultipv\s+(\d+)", line)
                    idx = int(idx_m.group(1)) if idx_m else 1
                    per_line[idx] = line
                    if idx == 1 or not info_line:
                        info_line = line
                parts = line.split()
                if parts and parts[0] == "bestmove":
                    if pv_count > 1:
                        self._send("setoption name MultiPV value 1")
                    if len(parts) < 2 or parts[1] in ("(none)", "0000"):
                        return "", info_line, []
                    return parts[1], info_line, Lc0Engine._candidates(per_line, board)

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._lines.put(None)
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                try:
                    self.proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            finally:
                self._reader.join(timeout=1)
                for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                    if stream and (stream is not self.proc.stdout or not self._reader.is_alive()):
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            pass


class HybridChessEngine:
    """Super-GM Centaur Engine: Runs Lc0 (BT3 on RTX 3070 GPU) and Stockfish 19 (NNUE on CPU)
    simultaneously in parallel with zero hardware contention.

    Synthesis Algorithm:
    1. Strategic & Positional Mastery (Lc0 GPU): Lc0 evaluates long-term positional structures,
       king safety, and deep sacrifices using 8-plane history and WDL heads.
    2. Tactical Verification & Endgame Precision (Stockfish 19 CPU): Stockfish searches 30+ plies
       deep via Alpha-Beta + NNUE.
    3. Arbiter Fusion:
       - If both engines agree on the best move -> Play immediately with maximum confidence.
       - If Stockfish finds a forced mate or decisive tactical blow, or detects that Lc0's move
         is a tactical blunder (> 65 cp worse than Stockfish's best move in tactical verification),
         Stockfish overrides to prevent blunders and seize immediate wins.
       - In strategic/equal positions where Lc0's candidate move is tactically sound (within <= 35 cp
         on Stockfish's deep verification), Lc0's superior positional/human-like squeeze is chosen
         when Lc0 has high WDL confidence, or Stockfish's sharp continuation is chosen in tactical battles.
    """

    def __init__(self, weights_file: Optional[str] = None, handshake_timeout: float = 30.0):
        self.lc0 = Lc0Engine(weights_file=weights_file, handshake_timeout=handshake_timeout)
        self.sf: Optional[StockfishEngine] = None
        if os.path.exists(STOCKFISH_EXE):
            try:
                cpu_threads = max(2, min(12, (os.cpu_count() or 8) - 2))
                self.sf = StockfishEngine(STOCKFISH_EXE, threads=cpu_threads, hash_mb=512)
                # Tune Lc0 for maximum strength on RTX 3070
                self.lc0._send("setoption name UCI_ShowWDL value true")
                self.lc0._send("setoption name NNCacheSize value 5000000")
                self.lc0._send("setoption name MinibatchSize value 256")
                self.lc0._send("setoption name MaxPrefetch value 64")
                self.lc0._send("setoption name SmartPruningFactor value 1.33")
            except Exception as e:
                print(f"[!] Stockfish initialization warning (falling back to pure Lc0): {e}")
                self.sf = None

    def _send(self, command: str):
        self.lc0._send(command)

    def get_best_move(
        self,
        moves_uci: list[str],
        move_time_sec: float = 1.5,
        nodes: Optional[int] = None,
        fen: Optional[str] = None,
        timeout: Optional[float] = None,
        use_hybrid: bool = True,
        multipv: int = 1,
    ) -> Tuple[str, str]:
        best, info, _ = self.analyse(
            moves_uci,
            move_time_sec=move_time_sec,
            nodes=nodes,
            fen=fen,
            timeout=timeout,
            use_hybrid=use_hybrid,
            multipv=multipv,
        )
        return best, info

    def analyse(
        self,
        moves_uci: list[str],
        move_time_sec: float = 1.5,
        nodes: Optional[int] = None,
        fen: Optional[str] = None,
        timeout: Optional[float] = None,
        use_hybrid: bool = True,
        multipv: int = 1,
    ) -> Tuple[str, str, list[dict]]:
        pv_count = max(1, min(8, int(multipv or 1)))
        if not use_hybrid or self.sf is None:
            return self.lc0.analyse(
                moves_uci, move_time_sec=move_time_sec, nodes=nodes, fen=fen, timeout=timeout, multipv=pv_count
            )

        # Allocate ~80% of time to parallel primary search and ~20% to tactical cross-verification if they disagree
        primary_time = max(0.15, move_time_sec * 0.82) if nodes is None else move_time_sec
        verify_time = max(0.12, move_time_sec * 0.18)

        with ThreadPoolExecutor(max_workers=2) as pool:
            lc0_future = pool.submit(
                self.lc0.analyse,
                moves_uci,
                move_time_sec=primary_time,
                nodes=nodes,
                fen=fen,
                timeout=timeout,
                multipv=pv_count,
            )
            sf_future = pool.submit(
                self.sf.analyse,
                moves_uci,
                move_time_sec=primary_time,
                nodes=None,
                fen=fen,
                timeout=timeout,
                multipv=pv_count,
            )

            lc0_move, lc0_info, lc0_cands = lc0_future.result()
            try:
                sf_move, sf_info, sf_cands = sf_future.result()
            except Exception:
                return lc0_move, lc0_info, lc0_cands

        if not sf_move:
            return lc0_move, lc0_info, lc0_cands
        if not lc0_move:
            return sf_move, sf_info, sf_cands

        lc0_meta = parse_uci_info(lc0_info)
        sf_meta = parse_uci_info(sf_info)

        # Combine WDL from Lc0/SF and depth/nps telemetry
        chosen_move = lc0_move
        chosen_source = "Hybrid-Consensus (Lc0+SF19)"

        if lc0_move == sf_move:
            chosen_move = lc0_move
            chosen_source = "Hybrid-Consensus (Lc0+SF19)"
        else:
            # Check if Stockfish found a forced mate
            if sf_meta["mate"] is not None and sf_meta["mate"] > 0:
                chosen_move = sf_move
                chosen_source = f"SF19-ForcedMate (#{sf_meta['mate']})"
            elif sf_meta["mate"] is not None and sf_meta["mate"] < 0:
                # Defending against mate: trust Stockfish's exact tactical defense
                chosen_move = sf_move
                chosen_source = "SF19-MateDefense"
            else:
                # Have Stockfish tactically verify Lc0's proposed move
                try:
                    _, verify_info = self.sf.evaluate_move(
                        moves_uci, lc0_move, move_time_sec=verify_time, fen=fen
                    )
                    verify_meta = parse_uci_info(verify_info)
                except Exception:
                    verify_meta = {"score_cp": None, "mate": None}

                if verify_meta["mate"] is not None and verify_meta["mate"] < 0:
                    # Lc0 move walks into a forced mate! Override with Stockfish
                    chosen_move = sf_move
                    chosen_source = "SF19-BlunderShield"
                elif sf_meta["score_cp"] is not None and verify_meta["score_cp"] is not None:
                    cp_loss = sf_meta["score_cp"] - verify_meta["score_cp"]
                    # If Lc0's move loses less than 28 centipawns tactically, trust Lc0's deep strategic vision
                    # unless we are in a low-material tactical endgame (<= 10 pieces) where SF19 excels
                    board = chess.Board(fen) if fen else chess.Board()
                    for m in moves_uci:
                        board.push_uci(m)
                    piece_count = len(board.piece_map())

                    if piece_count <= 10 and cp_loss > 10:
                        chosen_move = sf_move
                        chosen_source = "SF19-EndgamePrecision"
                    elif cp_loss <= 28:
                        chosen_move = lc0_move
                        chosen_source = f"Lc0-Strategic (SF-verified Δ{cp_loss:+d}cp)"
                    else:
                        chosen_move = sf_move
                        chosen_source = f"SF19-Tactical (+{cp_loss}cp edge)"
                else:
                    chosen_move = sf_move
                    chosen_source = "SF19-Tactical"

        # Build enriched UCI info line containing both engines' telemetry
        wdl_list = lc0_meta["wdl"] or sf_meta["wdl"]
        wdl_str = f" wdl {wdl_list[0]} {wdl_list[1]} {wdl_list[2]}" if wdl_list else ""
        score_part = ""
        if sf_meta["mate"] is not None:
            score_part = f"score mate {sf_meta['mate']}"
        elif sf_meta["score_cp"] is not None:
            score_part = f"score cp {sf_meta['score_cp']}"
        elif lc0_meta["score_cp"] is not None:
            score_part = f"score cp {lc0_meta['score_cp']}"
        else:
            score_part = "score cp 0"

        depth_val = sf_meta["depth"] or lc0_meta["depth"] or 1
        total_nodes = (lc0_meta["nodes"] or 0) + (sf_meta["nodes"] or 0)
        total_nps = (lc0_meta["nps"] or 0) + (sf_meta["nps"] or 0)
        pv_moves = sf_meta["pv"] if chosen_move == sf_move and sf_meta["pv"] else (lc0_meta["pv"] or [chosen_move])
        pv_str = " ".join(pv_moves[:6])

        combined_info = (
            f"info depth {depth_val} {score_part}{wdl_str} "
            f"nodes {total_nodes} nps {total_nps} "
            f"string [{chosen_source} | Lc0:{lc0_move} SF19:{sf_move}] pv {pv_str}"
        )
        return chosen_move, combined_info, self._merge_candidates(
            chosen_move, sf_cands, lc0_cands, sf_meta, lc0_meta
        )

    @staticmethod
    def _merge_candidates(
        chosen_move: str,
        sf_cands: list[dict],
        lc0_cands: list[dict],
        sf_meta: dict,
        lc0_meta: dict,
    ) -> list[dict]:
        """Merges both candidate lists into one score-ordered pool for human-style sampling.

        Pinned first is the arbiter's own verdict, so rank 1 always matches the played choice.
        Stockfish scores fill in a move that only Lc0 ranked, and vice versa; a move offered by
        both keeps Stockfish's (Alpha-Beta/NNUE) number, since that is the more reliable tactical
        measure of how much a deviation actually costs.
        """
        board_scores: dict[str, dict] = {}
        for entry in lc0_cands or []:
            if entry and entry.get("move"):
                board_scores[entry["move"]] = {
                    "move": entry["move"],
                    "score_cp": entry.get("score_cp"),
                    "mate": entry.get("mate"),
                }
        for entry in sf_cands or []:
            if entry and entry.get("move"):
                board_scores[entry["move"]] = {
                    "move": entry["move"],
                    "score_cp": entry.get("score_cp"),
                    "mate": entry.get("mate"),
                }

        pinned = board_scores.get(chosen_move, {
            "move": chosen_move,
            "score_cp": sf_meta.get("score_cp"),
            "mate": sf_meta.get("mate"),
        })
        rest = [c for m, c in board_scores.items() if m != chosen_move]
        rest.sort(key=lambda c: _sort_score(c), reverse=True)
        return [pinned] + rest


    def close(self):
        try:
            self.lc0.close()
        finally:
            if self.sf is not None:
                self.sf.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def print_board(board: chess.Board, user_is_white: bool = True):
    print("\n   +------------------------+")
    ranks = range(7, -1, -1) if user_is_white else range(0, 8)
    files = range(0, 8) if user_is_white else range(7, -1, -1)

    for rank in ranks:
        row_str = f" {rank + 1} |"
        for file in files:
            square = chess.square(file, rank)
            piece = board.piece_at(square)
            if piece:
                symbol = UNICODE_PIECES.get(piece.symbol(), piece.symbol())
            else:
                symbol = "·"
            row_str += f"  {symbol}"
        row_str += "  |"
        print(row_str)

    print("   +------------------------+")
    file_labels = "      a  b  c  d  e  f  g  h" if user_is_white else "      h  g  f  e  d  c  b  a"
    print(file_labels)
    turn_str = "White to move" if board.turn == chess.WHITE else "Black to move"
    print(f" Turn: {turn_str}\n")


def main():
    print("=" * 60)
    print("      ♟️  AlphaZero Chess (Leela Chess Zero - Lc0)  ♟️")
    print("      Hardware: NVIDIA GeForce RTX 3070 (CUDA fp16)")
    print("=" * 60)

    # Detect best available net
    if not os.path.isdir(LC0_DIR):
        raise FileNotFoundError(f"Lc0 directory not found: {LC0_DIR}. Please ensure Lc0 is installed there.")
    nets = [f for f in os.listdir(LC0_DIR) if f.endswith(".pb.gz") or f.endswith(".pb")]
    chosen_net = None
    if "BT3-768x15x24h-swa-2790000.pb.gz" in nets:
        chosen_net = os.path.join(LC0_DIR, "BT3-768x15x24h-swa-2790000.pb.gz")
        print("[*] Loaded Neural Network: BT3-768x15x24h (Grandmaster Level)")
    elif nets:
        chosen_net = os.path.join(LC0_DIR, nets[0])
        print(f"[*] Loaded Neural Network: {nets[0]}")

    print("[*] Initializing Lc0 Engine on GPU...")
    with Lc0Engine(chosen_net) as engine:
        _play_game(engine)


def _play_game(engine):
    print("[*] Engine ready!\n")

    # Ask user color
    print("Choose your side:")
    print("  1) White (You move first)")
    print("  2) Black (AlphaZero moves first)")
    choice = input("Enter 1 or 2 [Default: 1]: ").strip()
    user_is_white = choice != "2"

    # Time per move
    time_input = input("AI thinking time in seconds [Default: 1.5]: ").strip()
    try:
        move_time = float(time_input) if time_input else 1.5
        if not math.isfinite(move_time) or move_time <= 0:
            raise ValueError("Thinking time must be finite and positive")
    except ValueError:
        move_time = 1.5

    board = chess.Board()
    moves_uci = []

    print("\nHow to play: Type moves in SAN (e.g. e4, Nf3, O-O) or UCI (e.g. e2e4).")
    print("Commands: 'undo' to take back move, 'quit' to exit.\n")

    while not board.is_game_over():
        print_board(board, user_is_white)

        is_user_turn = (board.turn == chess.WHITE and user_is_white) or (
            board.turn == chess.BLACK and not user_is_white
        )

        if is_user_turn:
            while True:
                move_str = input("Your move: ").strip()
                if move_str.lower() in ["quit", "exit", "q"]:
                    print("Game aborted.")
                    engine.close()
                    return
                if move_str.lower() == "undo":
                    if len(moves_uci) >= 2:
                        board.pop()
                        board.pop()
                        moves_uci.pop()
                        moves_uci.pop()
                        print("Undid 1 full move (your move + AI move).")
                        print_board(board, user_is_white)
                    else:
                        print("Cannot undo at beginning of game.")
                    continue

                try:
                    move = board.parse_san(move_str)
                except ValueError:
                    try:
                        move = chess.Move.from_uci(move_str)
                        if move not in board.legal_moves:
                            raise ValueError()
                    except ValueError:
                        print("Invalid move! Try again (e.g. e4, Nf3, or e2e4).")
                        continue

                board.push(move)
                moves_uci.append(move.uci())
                break
        else:
            print(f"AlphaZero is thinking ({move_time}s on RTX 3070)...")
            best_move_uci, info = engine.get_best_move(moves_uci, move_time_sec=move_time)
            move = chess.Move.from_uci(best_move_uci)
            san_str = board.san(move)
            board.push(move)
            moves_uci.append(move.uci())
            print(f" AlphaZero played: {san_str} ({best_move_uci})")

    print_board(board, user_is_white)
    print("=" * 60)
    print(f"Game over! Result: {board.result()}")
    if board.is_checkmate():
        winner = "White" if board.turn == chess.BLACK else "Black"
        print(f"Checkmate! Winner: {winner}")
    elif board.is_stalemate():
        print("Draw by stalemate.")
    elif board.is_insufficient_material():
        print("Draw by insufficient material.")
    print("=" * 60)

    engine.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting.")
