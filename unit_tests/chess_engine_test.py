import subprocess
import sys
import time
import unittest
from unittest import mock

import chess

import play_chess

try:
    import play_chess_gui
except ImportError:
    play_chess_gui = None


FAKE_ENGINE = r'''
import sys
mode = sys.argv[1]
for raw in sys.stdin:
    command = raw.strip()
    if command == "uci":
        if mode == "silent_handshake":
            continue
        if mode == "die_handshake":
            sys.exit(0)
        if mode == "false_handshake":
            print("info string uciok", flush=True)
            continue
        print("uciok", flush=True)
    elif command == "isready":
        if mode == "silent_ready":
            continue
        print("readyok", flush=True)
    elif command.startswith("position "):
        position = command
    elif command.startswith("go "):
        if mode == "silent_search":
            continue
        if mode == "die_search":
            sys.exit(0)
        if mode == "noise_search":
            while True:
                print("info score cp 4", flush=True)
        if mode == "none":
            print("bestmove (none)", flush=True)
        elif mode == "illegal":
            print("bestmove e2e5", flush=True)
        elif mode.startswith("castle_"):
            print("info score cp 17 pv " + mode[7:], flush=True)
            print("bestmove " + mode[7:], flush=True)
        else:
            print("info score cp 17 string " + position, flush=True)
            print("bestmove " + ("e7e5" if "moves e2e4" in position else "e2e4"), flush=True)
'''


class ChessEngineTest(unittest.TestCase):
    def test_castling_engine_output_and_candidates_are_canonical(self):
        for side, raw, canonical in [("w", "e1h1", "e1g1"), ("w", "e1a1", "e1c1"),
                                     ("b", "e8h8", "e8g8"), ("b", "e8a8", "e8c8")]:
            with self.subTest(move=raw):
                engine = self.make_engine("castle_" + raw)
                move, _, candidates = engine.analyse([], fen=f"r3k2r/8/8/8/8/8/8/R3K2R {side} KQkq - 0 1", timeout=1)
                self.assertEqual(move, canonical)
                self.assertEqual(candidates[0]["move"], canonical)

    def make_engine(self, mode="success", handshake_timeout=1):
        popen = subprocess.Popen

        def start(*args, **kwargs):
            kwargs.pop("cwd", None)
            return popen([sys.executable, "-u", "-c", FAKE_ENGINE, mode], **kwargs)

        with mock.patch.object(play_chess.os.path, "exists", return_value=True), mock.patch.object(
            play_chess.subprocess, "Popen", side_effect=start
        ):
            engine = play_chess.Lc0Engine(handshake_timeout=handshake_timeout)
        self.addCleanup(engine.close)
        return engine

    def test_success_and_fen_with_moves(self):
        engine = self.make_engine()
        move, info = engine.get_best_move([], timeout=1)
        self.assertEqual(move, "e2e4")
        self.assertIn("score cp 17", info)
        move, info = engine.get_best_move(["e2e4"], fen=chess.STARTING_FEN, nodes=5, timeout=1)
        self.assertEqual(move, "e7e5")
        self.assertIn("position fen " + chess.STARTING_FEN + " moves e2e4", info)

    def test_handshake_failure_cleans_up(self):
        popen = subprocess.Popen
        processes = []
        for mode, error in (
            ("silent_handshake", TimeoutError), ("silent_ready", TimeoutError),
            ("false_handshake", TimeoutError), ("die_handshake", RuntimeError),
        ):
            with self.subTest(mode=mode):
                def start(*args, **kwargs):
                    kwargs.pop("cwd", None)
                    proc = popen([sys.executable, "-u", "-c", FAKE_ENGINE, mode], **kwargs)
                    processes.append(proc)
                    return proc

                before = time.monotonic()
                with mock.patch.object(play_chess.os.path, "exists", return_value=True), mock.patch.object(
                    play_chess.subprocess, "Popen", side_effect=start
                ), self.assertRaises(error):
                    play_chess.Lc0Engine(handshake_timeout=0.3)
                self.assertLess(time.monotonic() - before, 3)
                self.assertIsNotNone(processes[-1].poll())
                self.assertTrue(processes[-1].stdout.closed)

    def test_search_timeout_and_death(self):
        for mode, error in (
            ("silent_search", TimeoutError), ("noise_search", TimeoutError), ("die_search", RuntimeError),
        ):
            with self.subTest(mode=mode):
                engine = self.make_engine(mode)
                before = time.monotonic()
                with self.assertRaises(error):
                    engine.get_best_move([], nodes=5, timeout=0.15)
                self.assertLess(time.monotonic() - before, 3)
                self.assertIsNotNone(engine.proc.poll())
                self.assertFalse(engine._reader.is_alive())
                with self.assertRaises(RuntimeError):
                    engine.get_best_move([], timeout=0.1)

    def test_invalid_response(self):
        for mode in ("none", "illegal"):
            with self.subTest(mode=mode):
                engine = self.make_engine(mode)
                with self.assertRaises(RuntimeError):
                    engine.get_best_move([], timeout=1)
                self.assertIsNotNone(engine.proc.poll())

    def test_terminal_position_can_return_no_move(self):
        engine = self.make_engine("none")
        self.assertEqual(engine.get_best_move([], fen="8/8/8/8/8/1q6/2k5/K7 w - - 0 1", timeout=1)[0], "")

    def test_close_kills_when_wait_times_out(self):
        engine = self.make_engine()
        wait = engine.proc.wait
        calls = []

        def wait_once(timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired("fake engine", timeout)
            return wait(timeout=timeout)

        with mock.patch.object(engine.proc, "wait", side_effect=wait_once), mock.patch.object(
            engine.proc, "kill", wraps=engine.proc.kill
        ) as kill:
            engine.close()
            kill.assert_called_once()
        self.assertIsNotNone(engine.proc.poll())
        self.assertFalse(engine._reader.is_alive())

    def test_close_is_idempotent(self):
        engine = self.make_engine()
        engine.close()
        engine.close()
        self.assertIsNotNone(engine.proc.poll())
        self.assertTrue(engine.proc.stdin.closed)
        self.assertTrue(engine.proc.stdout.closed)
        with self.assertRaises(RuntimeError):
            engine._send("isready")


class ChessInputTest(unittest.TestCase):
    def test_thinking_time_input(self):
        cases = [(value, 1.5) for value in ("0", "-1", "nan", "inf", "-inf", "1e999", "bad", "")]
        cases.extend((("0.25", 0.25), ("2", 2.0), (" 3.5 ", 3.5)))
        for value, expected in cases:
            with self.subTest(value=value):
                engine = mock.Mock()
                engine.get_best_move.return_value = ("e2e4", "")
                with mock.patch("builtins.input", side_effect=["2", value, "quit"]), mock.patch(
                    "builtins.print"
                ), mock.patch.object(play_chess, "print_board"):
                    play_chess._play_game(engine)
                engine.get_best_move.assert_called_once_with(["e2e4"], move_time_sec=expected)
                engine.close.assert_called_once()


@unittest.skipIf(play_chess_gui is None, "Tkinter is unavailable")
class ChessGuiTest(unittest.TestCase):
    def make_app(self):
        app = play_chess_gui.ChessApp.__new__(play_chess_gui.ChessApp)
        app.board = chess.Board()
        app.moves_uci = []
        app.last_move = None
        app.ai_thinking = True
        app.status_label = mock.Mock()
        app.draw_board = mock.Mock()
        app.check_game_over = mock.Mock(return_value=False)
        app.root = mock.Mock()
        app.selected_level_key = "L7: Max AlphaZero (3500+)"
        return app

    def test_bad_results_recover_without_mutation(self):
        for move, error in (("", None), ("bad", None), ("e2e5", None), ("", "engine died")):
            with self.subTest(move=move, error=error):
                app = self.make_app()
                with mock.patch.object(play_chess_gui.messagebox, "showerror") as showerror:
                    app.on_ai_move_complete(move, error)
                self.assertFalse(app.ai_thinking)
                self.assertEqual(app.board.fen(), chess.STARTING_FEN)
                self.assertEqual(app.moves_uci, [])
                showerror.assert_called_once()

    def test_legal_result(self):
        app = self.make_app()
        app.on_ai_move_complete("e2e4")
        self.assertFalse(app.ai_thinking)
        self.assertEqual(app.moves_uci, ["e2e4"])
        self.assertEqual(app.board.peek(), chess.Move.from_uci("e2e4"))

    def test_worker_failure_delivered_on_ui_thread(self):
        for engine in (None, mock.Mock()):
            with self.subTest(engine=engine):
                app = self.make_app()
                app.ai_thinking = False
                app.engine = engine
                if engine is not None:
                    engine.get_best_move.side_effect = RuntimeError("search failed")
                workers = []

                def start_worker(target, daemon):
                    thread = mock.Mock()
                    thread.start.side_effect = lambda: workers.append(target)
                    return thread

                with mock.patch.object(play_chess_gui.threading, "Thread", side_effect=start_worker), mock.patch.object(
                    play_chess_gui.messagebox, "showerror"
                ) as showerror:
                    app.trigger_ai_move()
                    workers[0]()
                    app.root.after.call_args.args[1]()
                    self.assertFalse(app.ai_thinking)
                    showerror.assert_called_once()


if __name__ == "__main__":
    unittest.main()
