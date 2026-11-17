from absl.testing import absltest
from absl.testing import parameterized
import numpy as np
from unittest import mock

from alpha_zero.core import mcts_v2
from alpha_zero.envs.gomoku import GomokuEnv


class MctsV2Test(parameterized.TestCase):
    def test_large_visit_counts_produce_finite_policy(self):
        counts = np.array([1e9, 2e9, 3e9], dtype=np.float32)
        policy = mcts_v2.generate_search_policy(counts, 0.1, np.array([1, 1, 0], dtype=np.int8))
        np.testing.assert_allclose(policy, [1 / 33, 32 / 33, 0], rtol=1e-6)

    @parameterized.parameters(0, -1, 1.5, True)
    def test_invalid_parallel_count_rejected_before_evaluation(self, count):
        env = GomokuEnv(board_size=3, num_to_win=3)
        evaluate = mock.Mock(side_effect=AssertionError('Must validate before evaluation'))
        with self.assertRaises(ValueError):
            mcts_v2.parallel_uct_search(env, evaluate, None, 19652, 1.25, 2, count)
        evaluate.assert_not_called()

    @parameterized.product(eps=(0.0, 0.25, 1.0), zero_priors=(False, True))
    def test_noise_is_normalized_over_legal_moves(self, eps, zero_priors):
        node = mcts_v2.Node(1, 3, parent=mcts_v2.DummyNode())
        priors = np.zeros(3, dtype=np.float32) if zero_priors else np.array([0.2, 0.3, 0.5], dtype=np.float32)
        mcts_v2.expand(node, priors)
        mcts_v2.add_dirichlet_noise(node, np.array([1, 0, 1]), eps=eps, alpha=1.0)
        self.assertEqual(node.child_P[1], 0)
        self.assertAlmostEqual(node.child_P.sum(), 1.0)

    @parameterized.product(
        parallel=(False, True),
        num_simulations=(1, 16),
        deterministic=(False, True),
        warm_up=(False, True),
    )
    def test_search_policy(self, parallel, num_simulations, deterministic, warm_up):
        env = GomokuEnv(board_size=3, num_to_win=3)
        env.reset()
        env.step(0)
        legal_actions = env.legal_actions.copy()
        observation = env.observation().copy()
        prior = np.arange(env.action_dim, 0, -1, dtype=np.float32)
        prior /= prior.sum()
        evaluations = []

        def evaluate(obs, batched=False):
            evaluations.append(batched)
            if batched:
                return np.tile(prior, (obs.shape[0], 1)), [0.0] * obs.shape[0]
            return prior.copy(), 0.0

        search = mcts_v2.parallel_uct_search if parallel else mcts_v2.uct_search
        kwargs = {'num_parallel': 2} if parallel else {}
        move, policy, root_value, child_value, next_root = search(
            env,
            evaluate,
            None,
            c_puct_base=19652,
            c_puct_init=1.25,
            num_simulations=num_simulations,
            deterministic=deterministic,
            warm_up=warm_up,
            **kwargs,
        )

        self.assertEqual(legal_actions[move], 1)
        self.assertEqual(policy.shape, (env.action_dim,))
        self.assertTrue(np.all(np.isfinite(policy)))
        self.assertTrue(np.all(policy >= 0))
        np.testing.assert_allclose(policy.sum(), 1.0, rtol=1e-6)
        np.testing.assert_array_equal(policy[legal_actions == 0], 0.0)
        self.assertIsNotNone(next_root)
        self.assertGreater(next_root.N, 0)
        self.assertIsInstance(next_root.parent, mcts_v2.DummyNode)
        self.assertTrue(np.isfinite(root_value))
        self.assertTrue(np.isfinite(child_value))
        np.testing.assert_array_equal(env.legal_actions, legal_actions)
        np.testing.assert_array_equal(env.observation(), observation)
        if not parallel:
            self.assertLen(evaluations, max(2, num_simulations))
            if num_simulations == 1:
                self.assertEqual(move, 1)
                self.assertEqual(policy[move], 1.0)


if __name__ == '__main__':
    absltest.main()
