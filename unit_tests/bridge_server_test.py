import http.client
import json
import socket
import threading
import unittest
from http.server import HTTPServer
from unittest.mock import Mock, patch

import chess

import bridge_server


class BridgeServerTest(unittest.TestCase):
    def setUp(self):
        self.engine = Mock()
        self.engine.analyse.return_value = ("e7e5", "info score cp 12", [])
        self.engine_patch = patch.object(bridge_server, "engine", self.engine)
        self.engine_patch.start()
        self.server = HTTPServer(("127.0.0.1", 0), bridge_server.BridgeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.engine_patch.stop)
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, payload=None, body=None, headers=None):
        if body is None:
            body = json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request("POST", "/move", body=body, headers=headers or {})
            response = connection.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "*")
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            return response.status, result
        finally:
            connection.close()

    def assert_invalid(self, payload=None, **kwargs):
        self.engine.reset_mock()
        status, result = self.request(payload, **kwargs)
        self.assertEqual(status, 400, result)
        self.assertIn("error", result)
        self.assertEqual(self.engine.mock_calls, [])

    def test_history_san_and_engine_position(self):
        status, result = self.request({"moves": ["e2e4"]})
        self.assertEqual(status, 200)
        self.assertEqual(result["san"], "e5")
        self.assertFalse(result["in_check"])
        self.assertEqual(result["legal_move_count"], 20)
        self.assertEqual((result["from"], result["to"], result["promotion"]), ("e7", "e5", None))
        self.engine.analyse.assert_called_once_with(["e2e4"], move_time_sec=2.5, nodes=3500, fen=None, multipv=1)

    def test_fen_with_history(self):
        board = chess.Board()
        board.push_uci("e2e4")
        fen = board.fen()
        self.engine.analyse.return_value = ("g1f3", "", [])
        status, result = self.request({"fen": fen, "moves": ["e7e5"], "nodes": 0, "move_time": 0.25})
        self.assertEqual(status, 200)
        self.assertEqual(result["san"], "Nf3")
        self.engine.analyse.assert_called_once_with(["e7e5"], move_time_sec=0.25, nodes=None, fen=fen, multipv=1)

    def test_forced_check_reply_metadata(self):
        self.engine.analyse.return_value = ("h8g8", "", [])
        status, result = self.request({"fen": "7k/8/5K2/8/8/8/8/7R b - - 0 1"})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["in_check"])
        self.assertEqual(result["legal_move_count"], 1)

    def test_castling_notation_normalized_and_illegal_candidates_removed(self):
        for side, raw, canonical in [("w", "e1h1", "e1g1"), ("w", "e1a1", "e1c1"),
                                     ("b", "e8h8", "e8g8"), ("b", "e8a8", "e8c8")]:
            with self.subTest(move=raw):
                self.engine.analyse.return_value = (raw, "", [
                    {"move": raw}, {"move": canonical}, {"move": "a1a8q"}])
                status, result = self.request({"fen": f"r3k2r/8/8/8/8/8/8/R3K2R {side} KQkq - 0 1"})
                self.assertEqual(status, 200, result)
                self.assertEqual(result["bestmove"], canonical)
                self.assertEqual([c["move"] for c in result["candidates"]], [canonical])

    def test_rook_square_castling_history_is_normalized_before_engine(self):
        fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
        self.engine.analyse.return_value = ("e8c8", "", [])
        status, result = self.request({"fen": fen, "moves": ["e1h1"]})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.engine.analyse.call_args.args[0], ["e1g1"])

    def test_castling_without_rights_remains_rejected(self):
        self.assert_invalid({"fen": "r3k2r/8/8/8/8/8/8/R3K2R w - - 0 1", "moves": ["e1h1"]})

    def test_extension_fen_only_request(self):
        board = chess.Board()
        board.push_uci("e2e4")
        status, result = self.request({"fen": board.fen(), "level": 3})
        self.assertEqual(status, 200)
        self.assertEqual(result["san"], "e5")
        self.engine.analyse.assert_called_once_with([], move_time_sec=0.6, nodes=150, fen=board.fen(), multipv=1)

    def test_malformed_json_and_non_objects(self):
        for body in (b"{", b"", b"\xff", b"[]", b"null", b"true", b'"text"', b"123"):
            with self.subTest(body=body):
                self.assert_invalid(body=body)

    def test_invalid_fields_before_engine_commands(self):
        invalid = {
            "level": [None, True, 0, 9, 1.5, "7", float("inf"), float("nan")],
            "nodes": [None, True, -1, 1.5, "0", float("inf"), float("nan")],
            "move_time": [None, True, 0, -1, "1", float("inf"), float("-inf"), float("nan"), 10 ** 400],
            "temperature": [None, False, 0, -1, "1", float("inf"), float("nan"), 10 ** 400],
            "moves": [None, "e2e4", [1], [None], ["e2e5"], ["e7e5"], ["0000"], ["e2e4", "e2e4"], ["e2e4\ngo infinite"]],
            "fen": [None, 1, [], "invalid", "8/8/8/8/8/8/8/8 w - - 0 1", chess.STARTING_FEN + "\ngo infinite"],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_invalid({field: value})

    def test_invalid_history_relative_to_fen(self):
        board = chess.Board()
        board.push_uci("e2e4")
        self.assert_invalid({"fen": board.fen(), "moves": ["g1f3"]})

    def test_content_length_validation(self):
        for length in ("-1", "abc", "1.5", "+2", "1048577", "9" * 5000):
            with self.subTest(length=length):
                self.assert_invalid(body=b"{}", headers={"Content-Length": length})
        self.assert_invalid(body=b"{}", headers={"Transfer-Encoding": "chunked"})

    def test_missing_duplicate_and_truncated_content_length(self):
        for headers, body in (([], b""), (["Content-Length: 2", "Content-Length: 2"], b""),
                              (["Content-Length: 20"], b"{}")):
            with self.subTest(headers=headers):
                self.engine.reset_mock()
                connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
                try:
                    connection.putrequest("POST", "/move")
                    for header in headers:
                        key, value = header.split(": ", 1)
                        connection.putheader(key, value)
                    connection.endheaders(body)
                    connection.sock.shutdown(1)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    self.assertIn("error", json.loads(response.read()))
                    self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "*")
                    self.assertEqual(self.engine.mock_calls, [])
                finally:
                    connection.close()

    def test_incomplete_open_body_times_out_and_health_recovers(self):
        with patch.object(bridge_server.BridgeHandler, "timeout", 0.2), patch.object(
            self.server, "handle_error"
        ) as handle_error:
            with socket.create_connection(self.server.server_address, timeout=3) as stalled:
                stalled.sendall(b"POST /move HTTP/1.1\r\nHost: localhost\r\nContent-Length: 20\r\n\r\n{}")
                health = http.client.HTTPConnection(*self.server.server_address, timeout=3)
                try:
                    health.request("GET", "/health")
                    response = health.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read())["status"], "ready")
                    with http.client.HTTPResponse(stalled) as timed_out:
                        timed_out.begin()
                        self.assertEqual(timed_out.status, 408)
                        self.assertIn("timed out", json.loads(timed_out.read())["error"])
                        self.assertEqual(timed_out.getheader("Access-Control-Allow-Origin"), "*")
                    self.assertEqual(stalled.recv(1), b"")
                finally:
                    health.close()
            handle_error.assert_not_called()
            self.assertEqual(self.engine.mock_calls, [])

    def test_terminal_positions_do_not_search(self):
        for fen in ("7k/6Q1/5K2/8/8/8/8/8 b - - 0 1", "7k/5K2/6Q1/8/8/8/8/8 b - - 0 1"):
            with self.subTest(fen=fen):
                self.assert_invalid({"fen": fen})
        self.assert_invalid({"moves": ["f2f3", "e7e5", "g2g4", "d8h4"]})

    def test_invalid_engine_moves_are_errors(self):
        for move in ("", "0000", "(none)", "a1a8", "e2e4", None, 1):
            with self.subTest(move=move):
                self.engine.analyse.return_value = (move, "", [])
                status, result = self.request({"moves": ["e2e4"]})
                self.assertEqual(status, 502)
                self.assertIn("error", result)

    def test_engine_failure_returns_json_with_cors(self):
        self.engine.analyse.side_effect = RuntimeError("search failed")
        status, result = self.request({"moves": ["e2e4"]})
        self.assertEqual(status, 500)
        self.assertIn("error", result)


    def test_level_8_centaur_god_and_telemetry(self):
        self.engine.analyse.return_value = (
            "e7e5",
            "info depth 24 score cp 34 wdl 450 500 50 nodes 125000 nps 85000 pv e7e5 g1f3 b8c6",
            [{"move": "e7e5", "score_cp": 34, "mate": None}],
        )
        status, result = self.request({"moves": ["e2e4"], "level": 8})
        self.assertEqual(status, 200)
        self.assertEqual(result["bestmove"], "e7e5")
        self.assertEqual(result["ponder"], "g1f3")
        self.assertEqual(result["wdl"], [450, 500, 50])
        self.assertEqual(result["score_cp"], 34)
        self.assertEqual(result["depth"], 24)
        self.assertEqual(result["nodes"], 125000)
        self.assertEqual(result["nps"], 85000)
        self.assertEqual(result["pv"], ["e7e5", "g1f3", "b8c6"])
        self.assertEqual(result["candidates"], [{"move": "e7e5", "score_cp": 34, "mate": None}])

    def test_multipv_candidates_and_validation(self):
        self.engine.analyse.return_value = (
            "e7e5",
            "info depth 18 score cp 20 pv e7e5 g1f3",
            [
                {"move": "e7e5", "score_cp": 20, "mate": None},
                {"move": "c7c5", "score_cp": 12, "mate": None},
            ],
        )
        status, result = self.request({"moves": ["e2e4"], "multipv": 3})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][1]["move"], "c7c5")
        self.assertEqual(self.engine.analyse.call_args.kwargs["multipv"], 3)

        for bad in (0, 9, True, 1.5, "2"):
            with self.subTest(multipv=bad):
                self.assert_invalid({"multipv": bad})


if __name__ == "__main__":
    unittest.main()
