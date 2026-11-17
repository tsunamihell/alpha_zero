import unittest

import numpy as np

from alpha_zero.core.replay import Transition, UniformReplay


class TestReplayState(unittest.TestCase):
    def test_restore_wrapped_buffer_and_continue(self):
        replay = UniformReplay(2, np.random.RandomState(0), compress_data=False)
        for value in range(3):
            replay.add(Transition(np.array([value]), np.array([1]), value))
        restored = UniformReplay(2, np.random.RandomState(0), compress_data=False)
        restored.set_state(replay.get_state())
        restored.add(Transition(np.array([3]), np.array([1]), 3))
        self.assertEqual([item.value for item in restored.get([0, 1])], [2, 3])
        self.assertEqual(restored.sample(2).state.shape, (2, 1))

    def test_reject_capacity_mismatch_without_modification(self):
        replay = UniformReplay(2, np.random.RandomState(0), compress_data=False)
        state = replay.get_state()
        for capacity in (1, 3):
            restored = UniformReplay(capacity, np.random.RandomState(0), compress_data=False)
            with self.assertRaisesRegex(ValueError, 'capacity mismatch'):
                restored.set_state(state)
            self.assertEqual(len(restored.storage), capacity)
            self.assertEqual(restored.num_samples_added, 0)

    def test_reject_compression_mismatch(self):
        replay = UniformReplay(2, np.random.RandomState(0), compress_data=False)
        restored = UniformReplay(2, np.random.RandomState(0), compress_data=True)
        with self.assertRaisesRegex(ValueError, 'compression'):
            restored.set_state(replay.get_state())


if __name__ == '__main__':
    unittest.main()
