"""Regression tests shared by both MCTS implementations."""

from unittest import mock

from absl.testing import absltest, parameterized
import numpy as np

from alpha_zero.core import mcts_v1, mcts_v2
from alpha_zero.envs.base import BoardGameEnv
from alpha_zero.envs.gomoku import GomokuEnv


class PassEnv(BoardGameEnv):
    """Small two-ply game to exercise pass selection without Go's global board size."""

    def __init__(self, only_pass=False):
        super().__init__(board_size=2, has_pass_move=True)
        if only_pass:
            self.legal_actions[:-1] = 0

    def is_game_over(self):
        return self.steps >= 2

    def step(self, action):
        assert self.legal_actions[action]
        self.last_player = self.to_play
        self.to_play = self.opponent_player
        self.steps += 1
        return self.observation(), 0.0, self.is_game_over(), {}


class MctsSearchTest(parameterized.TestCase):
    def root(self, module, env, prior):
        if module is mcts_v2:
            root = module.Node(env.to_play, env.action_dim, parent=module.DummyNode())
            module.expand(root, prior)
        else:
            root = module.Node(env.to_play)
            module.expand(root, prior, env.opponent_player)
        module.backup(root, 0.0)
        return root

    def search(self, module, parallel, env, evaluate, root=None, **kwargs):
        search = module.parallel_uct_search if parallel else module.uct_search
        options = dict(num_simulations=2)
        if parallel:
            options['num_parallel'] = 2
        options.update(kwargs)
        return search(env, evaluate, root, 19652, 1.25, **options)

    @parameterized.product(module=(mcts_v1, mcts_v2), parallel=(False, True), only_pass=(False, True))
    def test_warmup_pass_policy_finishes_with_legal_move(self, module, parallel, only_pass):
        env = PassEnv(only_pass)
        prior = np.zeros(env.action_dim, dtype=np.float32)
        prior[env.pass_move] = 1

        def evaluate(obs, batched=False):
            return (np.tile(prior, (len(obs), 1)), [0.0] * len(obs)) if batched else (prior.copy(), 0.0)

        # Bound sampling attempts so the old rejection loop fails instead of hanging the suite.
        choose = np.random.choice
        calls = []

        def bounded_choice(*args, **kwargs):
            calls.append(1)
            if len(calls) > 5:
                raise AssertionError('Sampling did not terminate')
            return choose(*args, **kwargs)

        with mock.patch.object(module.np.random, 'choice', side_effect=bounded_choice):
            move, policy, _, _, _ = self.search(module, parallel, env, evaluate, warm_up=True)
        self.assertEqual(env.legal_actions[move], 1)
        self.assertEqual(move == env.pass_move, only_pass)
        self.assertAlmostEqual(policy.sum(), 1.0)
        self.assertEqual(env.steps, 0)

    @parameterized.product(module=(mcts_v1, mcts_v2), parallel=(False, True), count=(True, 1.5, float('inf'), float('nan'), 0))
    def test_invalid_simulation_budget_rejected_before_evaluation(self, module, parallel, count):
        evaluate = mock.Mock(side_effect=AssertionError('Invalid budget reached evaluation'))
        with self.assertRaises(ValueError):
            self.search(module, parallel, GomokuEnv(board_size=3, num_to_win=3), evaluate, num_simulations=count)
        evaluate.assert_not_called()

    @parameterized.product(module=(mcts_v1, mcts_v2), malformed=(False, True))
    def test_failed_batch_removes_all_virtual_losses(self, module, malformed):
        env = PassEnv(only_pass=True)
        prior = np.zeros(env.action_dim, dtype=np.float32)
        prior[env.pass_move] = 1
        root = self.root(module, env, prior)
        evaluate = mock.Mock(side_effect=[([], []), RuntimeError('Unbounded retry')]) if malformed else mock.Mock(
            side_effect=RuntimeError('Evaluation failed')
        )
        with self.assertRaises(ValueError if malformed else RuntimeError):
            self.search(module, True, env, evaluate, root)
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(root.losses_applied, 0)
        self.assertEqual(root.W, 0.0)
        self.assertEqual(root.N, 1)
        for child in root.children.values():
            self.assertEqual(child.losses_applied, 0)
            self.assertEqual(child.W, 0.0)
        # The same tree must remain usable after the failed batch.
        def healthy_evaluate(obs, batched=False):
            return (np.tile(prior, (len(obs), 1)), [0.0] * len(obs)) if batched else (prior.copy(), 0.0)
        result = self.search(module, True, env, healthy_evaluate, root)
        self.assertEqual(result[0], env.pass_move)

    @parameterized.parameters(mcts_v1, mcts_v2)
    def test_single_simulation_returns_normalized_policy(self, module):
        env = GomokuEnv(board_size=3, num_to_win=3)
        env.step(0)
        prior = np.ones(env.action_dim, dtype=np.float32) / env.action_dim
        result = self.search(module, False, env, lambda *args: (prior.copy(), 0.0), num_simulations=1)
        self.assertEqual(env.legal_actions[result[0]], 1)
        self.assertAlmostEqual(result[1].sum(), 1.0)

    @parameterized.product(module=(mcts_v1, mcts_v2), parallel=(False, True))
    def test_reuse_unvisited_warmup_move_with_root_noise(self, module, parallel):
        env = PassEnv()
        prior = np.zeros(env.action_dim, dtype=np.float32)
        prior[env.pass_move] = 1

        def evaluate(obs, batched=False):
            return (np.tile(prior, (len(obs), 1)), [0.0] * len(obs)) if batched else (prior.copy(), 0.0)

        move, _, _, _, root = self.search(module, parallel, env, evaluate, warm_up=True)
        env.step(move)
        result = self.search(module, parallel, env, evaluate, root, root_noise=True)
        self.assertEqual(env.legal_actions[result[0]], 1)
        self.assertAlmostEqual(result[1].sum(), 1.0)

    @parameterized.product(module=(mcts_v1, mcts_v2), parallel=(False, True))
    def test_complete_game_with_tree_reuse(self, module, parallel):
        env = GomokuEnv(board_size=3, num_to_win=3)
        prior = np.ones(env.action_dim, dtype=np.float32) / env.action_dim

        def evaluate(obs, batched=False):
            return (np.tile(prior, (len(obs), 1)), [0.0] * len(obs)) if batched else (prior.copy(), 0.0)

        root = None
        for _ in range(env.action_dim):
            move, policy, value, child_value, root = self.search(
                module, parallel, env, evaluate, root, num_simulations=16, root_noise=True, warm_up=True,
            )
            self.assertEqual(env.legal_actions[move], 1)
            self.assertAlmostEqual(policy.sum(), 1.0)
            self.assertLessEqual(abs(value), 1.0)
            self.assertLessEqual(abs(child_value), 1.0)
            self.assertEqual(root.losses_applied, 0)
            _, _, done, _ = env.step(move)
            if done:
                break
        self.assertTrue(env.is_game_over())

    @parameterized.parameters(mcts_v1, mcts_v2)
    def test_selection_failure_releases_previous_reservation(self, module):
        env = PassEnv(only_pass=True)
        prior = np.zeros(env.action_dim, dtype=np.float32)
        prior[env.pass_move] = 1
        root = self.root(module, env, prior)
        select = module.best_child
        calls = []

        def fail_second_selection(*args):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('Selection failed')
            return select(*args)

        with mock.patch.object(module, 'best_child', side_effect=fail_second_selection):
            with self.assertRaisesRegex(RuntimeError, 'Selection failed'):
                self.search(module, True, env, mock.Mock(), root)
        self.assertEqual(root.losses_applied, 0)
        self.assertEqual(root.W, 0.0)
        self.assertEqual(root.children[env.pass_move].losses_applied, 0)


if __name__ == '__main__':
    absltest.main()
