"""
replay_buffer.py
================
Experience replay: store training data from recent games and mix it into future
training sessions. This prevents "catastrophic forgetting" — the problem where
training on a new game causes the network to forget lessons from older games.

Think of it like a student reviewing old homework while doing new assignments.
Without replay, the network only ever learns from the most recent game, which
means knowledge gained from game 1 may be erased by game 50.

How it works:
  - After each game, we add that game's (board_tensor, target, weight) triples to the buffer.
  - The buffer has a fixed maximum size (max_games × max_positions_per_game).
  - When the buffer is full, the oldest game's data is dropped first (FIFO per game).
  - During training, we sample a random batch from the buffer and mix it with the
    current game's data so the network sees a blend of old and new experience.
"""

import random
from collections import deque


class ReplayBuffer:
    """Stores training samples (tensor, target, weight) from recent games.

    Each "game" is stored as a list of samples. When the buffer is full,
    the oldest game is dropped. This keeps the buffer to a fixed memory size
    while ensuring the most recent games have the highest representation.

    Args:
        max_games: how many past games to keep (default 100 games)
    """

    def __init__(self, max_games=100):
        self.max_games = max_games
        # deque automatically drops the leftmost (oldest) item when maxlen is exceeded
        self._games = deque(maxlen=max_games)

    def add_game(self, samples):
        """Store one game's training data in the buffer.

        Args:
            samples: list of (tensor, target_value, weight) tuples
                     tensor:       torch.Tensor of shape (input_size,) — the board encoding
                     target_value: float — the training target (win/loss signal)
                     weight:       float — how much this sample counts (TD weighting)
        """
        if samples:
            self._games.append(list(samples))

    def sample(self, n):
        """Randomly sample n training examples from the stored game history.

        Returns a list of (tensor, target_value, weight) tuples.
        If the buffer has fewer than n samples total, returns all of them.
        """
        # Flatten all games into a single pool of samples
        all_samples = [s for game in self._games for s in game]
        if not all_samples:
            return []
        # random.sample won't repeat indices; min() prevents requesting more than available
        return random.sample(all_samples, min(n, len(all_samples)))

    def total_samples(self):
        """Total number of individual training samples across all stored games."""
        return sum(len(g) for g in self._games)

    def __len__(self):
        """Number of games currently stored."""
        return len(self._games)


# Module-level singleton: one shared buffer for the whole backend process.
# learning.py imports this directly instead of creating its own instance.
_buffer = ReplayBuffer(max_games=100)


def get_buffer():
    """Return the shared replay buffer instance."""
    return _buffer
