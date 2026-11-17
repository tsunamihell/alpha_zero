"""Validation and move sampling shared by the MCTS implementations."""

import numpy as np


def validate_search_count(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < 1:
        raise ValueError(f'Expect `{name}` to be a positive integer, got {value}')


def sample_move(policy, legal_actions, avoid_move=None):
    """Sample once, excluding a warm-up pass only when another move is legal."""
    allowed = np.asarray(legal_actions) == 1
    if avoid_move is not None and np.count_nonzero(allowed) > int(allowed[avoid_move]):
        allowed[avoid_move] = False
    if not np.any(allowed):
        raise ValueError('Expect at least one legal action')
    probabilities = np.where(allowed, policy, 0.0).astype(np.float64)
    total = probabilities.sum()
    if total > 0:
        probabilities /= total
    else:
        # All visits may belong to pass; choose among the remaining legal moves.
        probabilities = allowed / np.count_nonzero(allowed)
    return np.random.choice(len(probabilities), p=probabilities)
