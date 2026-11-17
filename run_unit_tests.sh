#!/bin/bash

set -u -e  # Check for uninitialized variables and exit if any command fails.

# Run unit tests

python3 -m unit_tests.envs.base_test
python3 -m unit_tests.envs.gomoku_test
python3 -m unit_tests.envs.go_test
python3 -m unit_tests.transformation_test
python3 -m unit_tests.replay_test
python3 -m unit_tests.mcts_v2_test
python3 -m unit_tests.mcts_search_test
python3 -m unit_tests.bridge_server_test
python3 -m unit_tests.chess_engine_test
