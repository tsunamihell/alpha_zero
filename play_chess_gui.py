"""Graphical Chess GUI for AlphaZero (Leela Chess Zero - Lc0).
Play visually against AlphaZero with adjustable Difficulty / ELO strength (1000 to 3500+).
Runs locally on NVIDIA GeForce RTX 3070 GPU via CUDA.
"""

import os
import sys
import threading
import queue
import time
import tkinter as tk
from tkinter import messagebox
from typing import Optional
import chess

from play_chess import Lc0Engine, HybridChessEngine, LC0_DIR

# Modern Chess Board Colors
COLOR_LIGHT = "#EEEED2"
COLOR_DARK = "#769656"
COLOR_SELECTED = "#BACA44"
COLOR_MOVE_HINT = "#F7F782"
COLOR_LAST_MOVE = "#CDD26A"

UNICODE_PIECES = {
    "R": "♜", "N": "♞", "B": "♝", "Q": "♛", "K": "♚", "P": "♟",
    "r": "♖", "n": "♘", "b": "♗", "q": "♕", "k": "♔", "p": "♙",
}

STRENGTH_PRESETS = {
    "L1: Beginner (~1000)": {"nodes": 5, "temp": 2.4, "time": 0.2},
    "L2: Casual (~1400)": {"nodes": 25, "temp": 1.8, "time": 0.4},
    "L3: Intermediate (~1800)": {"nodes": 100, "temp": 1.4, "time": 0.7},
    "L4: Advanced (~2200)": {"nodes": 400, "temp": 1.1, "time": 1.2},
    "L5: Master (~2600)": {"nodes": 1500, "temp": 1.0, "time": 2.0},
    "L6: Super-GM (~3100)": {"nodes": 5000, "temp": 1.0, "time": 3.5},
    "L7: Max AlphaZero (3500+)": {"nodes": None, "temp": 1.0, "time": 5.0},
    "L8: Centaur God (Lc0+SF19 3800+)": {"nodes": None, "temp": 0.75, "time": 4.0},
}


class ChessApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AlphaZero Chess (Lc0) - Adjustable Difficulty (RTX 3070)")
        self.root.resizable(False, False)

        self.board = chess.Board()
        self.moves_uci: list[str] = []
        self.selected_square: Optional[chess.Square] = None
        self.legal_targets: list[chess.Square] = []
        self.last_move: Optional[chess.Move] = None

        self.user_color = chess.WHITE
        self.ai_thinking = False
        self.selected_level_key = "L7: Max AlphaZero (3500+)"

        self.engine: Optional[Lc0Engine] = None
        self.init_engine()

        self.setup_ui()
        self.draw_board()

    def init_engine(self):
        try:
            nets = [f for f in os.listdir(LC0_DIR) if f.endswith(".pb.gz") or f.endswith(".pb")]
            chosen_net = None
            if "BT3-768x15x24h-swa-2790000.pb.gz" in nets:
                chosen_net = os.path.join(LC0_DIR, "BT3-768x15x24h-swa-2790000.pb.gz")
            elif nets:
                chosen_net = os.path.join(LC0_DIR, nets[0])
            self.engine = HybridChessEngine(chosen_net)
        except Exception as e:
            messagebox.showerror("Engine Error", f"Failed to start Lc0 engine: {e}")

    def setup_ui(self):
        main_frame = tk.Frame(self.root, bg="#312E2B", padx=15, pady=15)
        main_frame.pack()

        # Top Bar
        top_bar = tk.Frame(main_frame, bg="#312E2B")
        top_bar.pack(fill=tk.X, pady=(0, 10))

        self.status_label = tk.Label(
            top_bar,
            text="White to move (Your turn)",
            font=("Segoe UI", 12, "bold"),
            fg="#FFFFFF",
            bg="#312E2B",
        )
        self.status_label.pack(side=tk.LEFT)

        # Strength Selector in Top Bar
        level_frame = tk.Frame(top_bar, bg="#312E2B")
        level_frame.pack(side=tk.RIGHT)
        tk.Label(level_frame, text="Power Level:", font=("Segoe UI", 9, "bold"), fg="#81b64c", bg="#312E2B").pack(side=tk.LEFT, padx=3)
        self.level_var = tk.StringVar(value=self.selected_level_key)
        level_menu = tk.OptionMenu(level_frame, self.level_var, *STRENGTH_PRESETS.keys(), command=self.on_level_change)
        level_menu.config(bg="#4F4B46", fg="#FFF", activebackground="#656059", relief=tk.FLAT, font=("Segoe UI", 9))
        level_menu["menu"].config(bg="#4F4B46", fg="#FFF")
        level_menu.pack(side=tk.LEFT)

        # Chessboard Canvas
        self.square_size = 76
        canvas_dim = self.square_size * 8

        self.canvas = tk.Canvas(
            main_frame,
            width=canvas_dim,
            height=canvas_dim,
            highlightthickness=2,
            highlightbackground="#444",
        )
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.on_canvas_click)

        # Bottom Bar
        bottom_bar = tk.Frame(main_frame, bg="#312E2B")
        bottom_bar.pack(fill=tk.X, pady=(12, 0))

        btn_style = {
            "font": ("Segoe UI", 10, "bold"),
            "bg": "#4F4B46",
            "fg": "#FFFFFF",
            "activebackground": "#656059",
            "activeforeground": "#FFFFFF",
            "relief": tk.FLAT,
            "padx": 10,
            "pady": 5,
        }

        tk.Button(bottom_bar, text="New Game", command=self.reset_game, **btn_style).pack(side=tk.LEFT, padx=3)
        tk.Button(bottom_bar, text="Switch Sides", command=self.switch_sides, **btn_style).pack(side=tk.LEFT, padx=3)
        tk.Button(bottom_bar, text="Undo Move", command=self.undo_move, **btn_style).pack(side=tk.LEFT, padx=3)

    def on_level_change(self, val):
        self.selected_level_key = val
        self.status_label.config(text=f"Difficulty: {val}")

    def square_to_canvas_coords(self, square: chess.Square) -> tuple[int, int]:
        file = chess.square_file(square)
        rank = chess.square_rank(square)
        col = file if self.user_color == chess.WHITE else (7 - file)
        row = (7 - rank) if self.user_color == chess.WHITE else rank
        return col * self.square_size, row * self.square_size

    def canvas_coords_to_square(self, x: int, y: int) -> Optional[chess.Square]:
        col = x // self.square_size
        row = y // self.square_size
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        file = col if self.user_color == chess.WHITE else (7 - col)
        rank = (7 - row) if self.user_color == chess.WHITE else row
        return chess.square(file, rank)

    def draw_board(self):
        self.canvas.delete("all")

        for rank in range(8):
            for file in range(8):
                square = chess.square(file, rank)
                x, y = self.square_to_canvas_coords(square)

                is_light = (file + rank) % 2 == 1
                color = COLOR_LIGHT if is_light else COLOR_DARK

                if self.last_move and square in (self.last_move.from_square, self.last_move.to_square):
                    color = COLOR_LAST_MOVE
                if self.selected_square == square:
                    color = COLOR_SELECTED
                elif square in self.legal_targets:
                    color = COLOR_MOVE_HINT

                self.canvas.create_rectangle(
                    x, y, x + self.square_size, y + self.square_size,
                    fill=color, outline=""
                )

                if square in self.legal_targets:
                    center_x = x + self.square_size // 2
                    center_y = y + self.square_size // 2
                    if self.board.piece_at(square):
                        self.canvas.create_oval(
                            x + 4, y + 4, x + self.square_size - 4, y + self.square_size - 4,
                            outline="#444", width=3
                        )
                    else:
                        self.canvas.create_oval(
                            center_x - 8, center_y - 8, center_x + 8, center_y + 8,
                            fill="#555", outline=""
                        )

                piece = self.board.piece_at(square)
                if piece:
                    symbol = UNICODE_PIECES.get(piece.symbol(), "")
                    center_x = x + self.square_size // 2
                    center_y = y + self.square_size // 2

                    is_white_piece = piece.color == chess.WHITE
                    fill_color = "#FFFFFF" if is_white_piece else "#1B1B1B"
                    shadow_color = "#444444" if is_white_piece else "#000000"

                    self.canvas.create_text(
                        center_x + 1, center_y + 1,
                        text=symbol, font=("Segoe UI Symbol", 44, "bold"),
                        fill=shadow_color
                    )
                    self.canvas.create_text(
                        center_x, center_y,
                        text=symbol, font=("Segoe UI Symbol", 44, "bold"),
                        fill=fill_color
                    )

    def on_canvas_click(self, event):
        if self.ai_thinking or self.board.is_game_over():
            return

        square = self.canvas_coords_to_square(event.x, event.y)
        if square is None:
            return

        is_user_turn = (self.board.turn == self.user_color)
        if not is_user_turn:
            return

        piece = self.board.piece_at(square)

        if piece and piece.color == self.user_color:
            self.selected_square = square
            self.legal_targets = [
                m.to_square for m in self.board.legal_moves if m.from_square == square
            ]
            self.draw_board()
            return

        if self.selected_square is not None and square in self.legal_targets:
            move = chess.Move(self.selected_square, square)
            if self.board.piece_type_at(self.selected_square) == chess.PAWN:
                if (self.user_color == chess.WHITE and chess.square_rank(square) == 7) or \
                   (self.user_color == chess.BLACK and chess.square_rank(square) == 0):
                    move.promotion = chess.QUEEN

            self.execute_user_move(move)
            return

        self.selected_square = None
        self.legal_targets = []
        self.draw_board()

    def execute_user_move(self, move: chess.Move):
        self.last_move = move
        self.board.push(move)
        self.moves_uci.append(move.uci())
        self.selected_square = None
        self.legal_targets = []
        self.draw_board()

        if self.check_game_over():
            return

        self.trigger_ai_move()

    def trigger_ai_move(self):
        if self.ai_thinking:
            return
        self.ai_thinking = True
        preset = STRENGTH_PRESETS.get(self.selected_level_key, STRENGTH_PRESETS["L7: Max AlphaZero (3500+)"])
        self.status_label.config(text=f"AI thinking ({self.selected_level_key})...")
        results = queue.Queue()
        moves = list(self.moves_uci)

        def worker():
            try:
                if self.engine is None:
                    raise RuntimeError("Lc0 engine is unavailable")
                self.engine._send(f"setoption name PolicyTemperature value {preset['temp']}")
                best_move_uci, _ = self.engine.get_best_move(
                    moves, move_time_sec=preset["time"], nodes=preset["nodes"]
                )
                results.put((best_move_uci, None))
            except Exception as e:
                results.put(("", str(e)))

        def poll_result():
            try:
                move, error = results.get_nowait()
            except queue.Empty:
                self.root.after(50, poll_result)
                return
            self.on_ai_move_complete(move, error)

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(50, poll_result)

    def on_ai_move_complete(self, best_move_uci: str, error: Optional[str] = None):
        self.ai_thinking = False
        try:
            if error is not None:
                raise RuntimeError(error)
            if not best_move_uci:
                if self.check_game_over():
                    return
                raise ValueError("Lc0 returned no move")
            move = chess.Move.from_uci(best_move_uci)
            if move not in self.board.legal_moves:
                raise ValueError(f"Lc0 returned an illegal move: {best_move_uci}")
        except (RuntimeError, ValueError) as e:
            self.status_label.config(text="Engine error - start a new game or undo")
            messagebox.showerror("Engine Error", str(e))
            return
        self.last_move = move
        self.board.push(move)
        self.moves_uci.append(move.uci())

        self.draw_board()
        if not self.check_game_over():
            self.status_label.config(text="Your turn")

    def check_game_over(self) -> bool:
        if self.board.is_game_over():
            res = self.board.result()
            winner = "White" if self.board.turn == chess.BLACK else "Black"
            msg = f"Game Over! Result: {res}"
            if self.board.is_checkmate():
                msg = f"Checkmate! Winner: {winner}"
            elif self.board.is_stalemate():
                msg = "Draw by Stalemate"
            self.status_label.config(text=msg)
            messagebox.showinfo("Game Over", msg)
            return True
        return False

    def reset_game(self):
        if self.ai_thinking:
            return
        self.board.reset()
        self.moves_uci.clear()
        self.selected_square = None
        self.legal_targets.clear()
        self.last_move = None
        self.status_label.config(text="White to move" if self.user_color == chess.WHITE else "AI thinking...")
        self.draw_board()

        if self.user_color == chess.BLACK:
            self.trigger_ai_move()

    def switch_sides(self):
        if self.ai_thinking:
            return
        self.user_color = chess.BLACK if self.user_color == chess.WHITE else chess.WHITE
        self.reset_game()

    def undo_move(self):
        if self.ai_thinking or len(self.moves_uci) < 2:
            return
        self.board.pop()
        self.board.pop()
        self.moves_uci.pop()
        self.moves_uci.pop()
        self.last_move = self.board.peek() if self.board.move_stack else None
        self.selected_square = None
        self.legal_targets.clear()
        self.status_label.config(text="Your turn")
        self.draw_board()

    def on_close(self):
        if self.engine:
            self.engine.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ChessApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
