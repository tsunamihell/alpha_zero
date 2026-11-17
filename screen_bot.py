"""Screen Extractor & Auto-Player Bot for Chess.
Captures any chessboard on screen using high-speed screen grab (mss),
detects moves via square pixel difference analysis,
calculates Grandmaster moves with Leela Chess Zero (Lc0) on GPU,
and automatically moves the mouse to play moves via PyAutoGUI.
"""

import json
import os
import sys
import time
from typing import Optional, Tuple
import chess
import numpy as np

try:
    import mss
    import pyautogui
    import cv2
except ImportError:
    pass

from play_chess import Lc0Engine, LC0_DIR

CONFIG_FILE = "screen_config.json"


class ScreenChessBot:
    def __init__(self):
        self.board_rect: Optional[dict] = None  # {left, top, width, height}
        self.user_color = chess.WHITE
        self.board = chess.Board()
        self.moves_uci: list[str] = []
        self.move_delay = 0.6  # delay in seconds before clicking

        # Engine
        nets = [f for f in os.listdir(LC0_DIR) if f.endswith(".pb.gz") or f.endswith(".pb")]
        chosen_net = None
        if "BT3-768x15x24h-swa-2790000.pb.gz" in nets:
            chosen_net = os.path.join(LC0_DIR, "BT3-768x15x24h-swa-2790000.pb.gz")
        elif nets:
            chosen_net = os.path.join(LC0_DIR, nets[0])

        print("[*] Initializing Lc0 Engine on RTX 3070 CUDA...")
        self.engine = Lc0Engine(chosen_net)
        print("[*] Engine ready!")

        self.sct = mss.mss()
        self.load_config()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.board_rect = json.load(f)
                    print(f"[*] Loaded existing board calibration: {self.board_rect}")
            except Exception:
                self.board_rect = None

    def save_config(self):
        if self.board_rect:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.board_rect, f, indent=2)
            print(f"[*] Calibration saved to {CONFIG_FILE}")

    def calibrate_board(self):
        print("\n" + "=" * 60)
        print("          🎯 Chessboard Screen Calibration")
        print("=" * 60)
        print("1. Open the game window so the chessboard is fully visible.")
        print("2. Move your mouse to the TOP-LEFT corner of square A8 (or H1 if flipped).")
        print("   You have 5 seconds...")
        for i in range(5, 0, -1):
            print(f"   {i}...", end="\r")
            time.sleep(1)
        x1, y1 = pyautogui.position()
        print(f"\n[+] Recorded Top-Left Corner at: ({x1}, {y1})")

        print("\n3. Move your mouse to the BOTTOM-RIGHT corner of square H1 (or A8 if flipped).")
        print("   You have 5 seconds...")
        for i in range(5, 0, -1):
            print(f"   {i}...", end="\r")
            time.sleep(1)
        x2, y2 = pyautogui.position()
        print(f"\n[+] Recorded Bottom-Right Corner at: ({x2}, {y2})")

        left = min(x1, x2)
        top = min(y1, y2)
        width = abs(x2 - x1)
        height = abs(y2 - y1)

        self.board_rect = {
            "left": int(left),
            "top": int(top),
            "width": int(width),
            "height": int(height)
        }
        self.save_config()
        print("\n[✓] Calibration complete!")

    def get_square_center(self, square: chess.Square) -> Tuple[int, int]:
        file = chess.square_file(square)  # 0=a .. 7=h
        rank = chess.square_rank(square)  # 0=1 .. 7=8

        sq_w = self.board_rect["width"] / 8
        sq_h = self.board_rect["height"] / 8

        if self.user_color == chess.WHITE:
            col = file
            row = 7 - rank
        else:
            col = 7 - file
            row = rank

        cx = int(self.board_rect["left"] + col * sq_w + sq_w / 2)
        cy = int(self.board_rect["top"] + row * sq_h + sq_h / 2)
        return cx, cy

    def capture_board_image(self) -> np.ndarray:
        sct_img = self.sct.grab(self.board_rect)
        img = np.array(sct_img)
        # Convert BGRA to BGR
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    def extract_square_cells(self, img: np.ndarray) -> list[np.ndarray]:
        h, w = img.shape[:2]
        sq_h = h // 8
        sq_w = w // 8
        cells = []
        for rank in range(7, -1, -1):
            for file in range(8):
                if self.user_color == chess.WHITE:
                    r = 7 - rank
                    c = file
                else:
                    r = rank
                    c = 7 - file
                cell = img[r*sq_h:(r+1)*sq_h, c*sq_w:(c+1)*sq_w]
                cells.append(cell)
        return cells

    def click_move(self, from_sq: chess.Square, to_sq: chess.Square):
        from_x, from_y = self.get_square_center(from_sq)
        to_x, to_y = self.get_square_center(to_sq)

        # Smooth, realistic cursor movement
        pyautogui.moveTo(from_x, from_y, duration=0.15)
        pyautogui.click()
        time.sleep(0.08)
        pyautogui.moveTo(to_x, to_y, duration=0.18)
        pyautogui.click()

    def run_auto_bot(self):
        if not self.board_rect:
            self.calibrate_board()

        print("\n" + "=" * 60)
        print("          🤖 Screen Auto-Player Running")
        print("=" * 60)
        print("Select your side:")
        print("  1) White (You move first, or bot plays White)")
        print("  2) Black (Opponent moves first, or bot plays Black)")
        side_choice = input("Enter 1 or 2 [Default: 1]: ").strip()
        self.user_color = chess.BLACK if side_choice == "2" else chess.WHITE

        print("\nChoose Operation Mode:")
        print("  1) Fully Autonomous Bot (Plays all moves automatically)")
        print("  2) Move Suggestions Only (Prints best moves on screen without clicking)")
        mode_choice = input("Enter 1 or 2 [Default: 1]: ").strip()
        is_autonomous = mode_choice != "2"

        print("\n[!] BOT IS ACTIVE. Press Ctrl+C in this terminal anytime to stop.")

        prev_img = self.capture_board_image()
        prev_cells = self.extract_square_cells(prev_img)

        # If bot plays White and is autonomous, play first move!
        if self.user_color == chess.WHITE and is_autonomous:
            print("[*] Bot plays White first move...")
            best_move_str, _ = self.engine.get_best_move(self.moves_uci, move_time_sec=1.0)
            move = chess.Move.from_uci(best_move_str)
            print(f"[>] Playing move: {move.uci()} ({self.board.san(move)})")
            self.click_move(move.from_square, move.to_square)
            self.board.push(move)
            self.moves_uci.append(move.uci())
            time.sleep(0.5)
            prev_img = self.capture_board_image()
            prev_cells = self.extract_square_cells(prev_img)

        while not self.board.is_game_over():
            time.sleep(0.3)
            curr_img = self.capture_board_image()
            curr_cells = self.extract_square_cells(curr_img)

            # Detect which squares had significant visual changes
            diff_squares = []
            for sq in range(64):
                # Calculate mean absolute difference
                diff = np.mean(cv2.absdiff(prev_cells[sq], curr_cells[sq]))
                if diff > 15.0:  # Threshold for piece movement
                    diff_squares.append(sq)

            # Move detected! Usually exactly 2 squares change (or 4 in castling)
            if 2 <= len(diff_squares) <= 4:
                # Find matching legal move in chess.Board
                matched_move = None
                for move in self.board.legal_moves:
                    if move.from_square in diff_squares and move.to_square in diff_squares:
                        matched_move = move
                        break

                if matched_move:
                    san_str = self.board.san(matched_move)
                    self.board.push(matched_move)
                    self.moves_uci.append(matched_move.uci())
                    print(f"\n[Detected Move]: {matched_move.uci()} ({san_str})")

                    prev_cells = curr_cells

                    # Now it's our turn to play!
                    if self.board.turn == self.user_color or is_autonomous:
                        print("[*] AlphaZero computing best move on RTX 3070...")
                        best_move_str, info = self.engine.get_best_move(self.moves_uci, move_time_sec=1.0)
                        ai_move = chess.Move.from_uci(best_move_str)
                        ai_san = self.board.san(ai_move)
                        print(f"[Best Move]: {best_move_str} ({ai_san})")

                        if is_autonomous:
                            time.sleep(self.move_delay)
                            self.click_move(ai_move.from_square, ai_move.to_square)
                            self.board.push(ai_move)
                            self.moves_uci.append(ai_move.uci())
                            time.sleep(0.5)
                            # Update reference image after our move
                            prev_img = self.capture_board_image()
                            prev_cells = self.extract_square_cells(prev_img)


def main():
    bot = ScreenChessBot()
    print("\nOptions:")
    print("  1) Run Screen Auto-Player")
    print("  2) Re-calibrate Screen Chessboard Region")
    ch = input("Enter 1 or 2 [Default: 1]: ").strip()
    if ch == "2":
        bot.calibrate_board()
    bot.run_auto_bot()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
