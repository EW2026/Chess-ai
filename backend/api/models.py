from django.db import models


class Player(models.Model):
    name = models.CharField(max_length=50, unique=True)
    elo = models.IntegerField(default=1200)

    # Personality traits — updated after each game to adapt AI behavior
    aggression = models.FloatField(default=0.5)   # 0 = defensive play style, 1 = aggressive
    risk = models.FloatField(default=0.5)         # chance of ignoring the DB move and searching fresh
    learning_rate = models.FloatField(default=0.1)

    def update_elo(self, opponent_elo, result):
        """Update ELO using the standard FIDE formula.
        expected = probability of winning based on rating difference.
        result = 1.0 (win), 0.5 (draw), 0.0 (loss).

        WHY K=16 INSTEAD OF THE STANDARD K=32:
        K=32 is FIDE's rate for new and developing players — it is intentionally
        aggressive so a player's rating finds its true level quickly in a tournament
        setting. Here we want the opposite: a slow, forgiving rating curve so the
        player has time to improve before facing a noticeably stronger AI.
        K=16 (used by FIDE for established players above 2400) cuts each per-game
        swing roughly in half:
          K=32 at equal strength → ~16 ELO per game
          K=16 at equal strength → ~8 ELO per game
        At K=16, the AI crossing from 1200 to 1600 requires roughly 50 consecutive
        wins rather than 25 — a much more gradual and fair challenge curve."""
        expected = 1 / (1 + 10 ** ((opponent_elo - self.elo) / 400))
        self.elo = int(self.elo + 16 * (result - expected))
        self.save()

    def __str__(self):
        return f"{self.name} ({self.elo})"


class PositionMemory(models.Model):
    """Stores board positions the AI has seen and what move it played, along with outcomes.
    Used as a fast lookup table — if we've been in this position before and know a good move,
    we skip the expensive minimax search entirely."""
    fen = models.CharField(max_length=200)
    hash = models.CharField(max_length=64, db_index=True)  # Zobrist hash for fast lookup

    move = models.CharField(max_length=10)  # UCI format e.g. "e2e4"

    wins = models.IntegerField(default=0)
    losses = models.IntegerField(default=0)
    times_seen = models.IntegerField(default=1)

    # Tracks when this position was last encountered so we can apply time-decay
    # to old win/loss statistics (item 16). Recent games are more relevant than
    # games from months ago when the player's skill level was different.
    last_seen = models.DateTimeField(auto_now=True)

    def win_rate(self):
        """Fraction of times this move led to a win. Returns 0 if never played."""
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0

    def score(self):
        """Net wins per appearance, decayed by how long ago this position was last seen.

        Decay factor: 0.95 per day since last_seen.
        A move seen 30 days ago that won 10 times scores: (10/times_seen) × 0.95^30 ≈ 21%
        The same move seen today still scores: (10/times_seen) × 1.0 = full value.

        This makes the AI adapt faster to an improving player instead of relying on
        stale data from when the player was at a different skill level.
        """
        from django.utils import timezone
        days_old = max(0, (timezone.now() - self.last_seen).days)
        recency  = 0.95 ** days_old           # exponential decay: 0.95 per day
        base     = (self.wins - self.losses) / max(1, self.times_seen)
        return base * recency

    class Meta:
        unique_together = ("hash", "move")  # one row per (position, move) pair
        indexes = [
            models.Index(fields=["hash"]),
        ]

    def __str__(self):
        return f"{self.hash[:10]} -> {self.move}"


class LearningTracker(models.Model):
    """One row per completed game. Records training statistics and the AI's ELO
    at the time of training so we can track the AI's improvement over time."""
    timestamp = models.DateTimeField(auto_now_add=True)
    won = models.BooleanField()
    positions_trained = models.IntegerField()
    final_loss = models.FloatField()
    avg_loss = models.FloatField()
    total_weight_changes = models.IntegerField()
    avg_weight_change = models.FloatField()
    ai_elo = models.IntegerField(default=1200)

    # ── Game context ───────────────────────────────────────────────────────────
    difficulty          = models.CharField(max_length=20, blank=True, default='')
    ai_color            = models.CharField(max_length=1,  blank=True, default='b')
    total_moves         = models.IntegerField(default=0)
    player_elo_snapshot = models.IntegerField(default=0)   # player ELO at game time

    # ── Search depth ───────────────────────────────────────────────────────────
    avg_depth  = models.FloatField(default=0.0)   # average completed search depth per move
    max_depth  = models.IntegerField(default=0)   # deepest single-move search this game

    # ── Move timing ────────────────────────────────────────────────────────────
    avg_move_time_ms    = models.FloatField(default=0.0)
    # Per-5-move segment breakdown: [{segment, moves, avg_ms, avg_depth}, ...]
    move_time_segments  = models.JSONField(default=list)

    # ── Move source ────────────────────────────────────────────────────────────
    db_hits       = models.IntegerField(default=0)   # moves served from PositionMemory
    opening_moves = models.IntegerField(default=0)   # moves served from opening book
    tt_hit_rate   = models.FloatField(default=0.0)   # fraction of moves with a warm TT root

    # ── Thermal / hardware ─────────────────────────────────────────────────────
    avg_workers   = models.FloatField(default=0.0)   # average active worker count during game
    min_workers   = models.IntegerField(default=0)   # worst throttle point
    avg_clock_mhz = models.FloatField(default=0.0)   # average sustained CPU clock

    # ── Aggression ─────────────────────────────────────────────────────────────
    ai_best_move_pct  = models.FloatField(default=0.0)  # % of moves AI played its true best
    player_aggression = models.FloatField(default=0.5)  # player.aggression snapshot at game end

    # ── NN training quality ────────────────────────────────────────────────────
    nn_weight        = models.FloatField(default=0.0)   # PST/NN blend ratio at training time
    nn_architecture  = models.CharField(max_length=40, blank=True, default='')  # e.g. "64→128→64→1"
    nn_param_count   = models.IntegerField(default=0)   # total trainable parameters

    def __str__(self):
        result = "win" if self.won else "loss"
        return f"Game {self.pk} ({result}, {self.difficulty}) — depth {self.avg_depth:.1f}, ELO: {self.ai_elo} @ {self.timestamp:%Y-%m-%d %H:%M}"


class IssueLog(models.Model):
    """Error log for chess engine problems. Written by log_issue() in minimax.py
    and the ai_move view whenever something unexpected happens (illegal move generated,
    board state mismatch, null move returned, etc.). Queryable via the /issue-log/ endpoint."""
    ISSUE_TYPES = [
        ("illegal_move_db",         "Illegal move from DB"),
        ("illegal_move_search",     "Illegal move from search"),
        ("illegal_move_view",       "Illegal move blocked in view"),
        ("board_mismatch",          "Board mismatch (moves vs FEN)"),
        ("move_replay_error",       "Move replay error in history"),
        ("frontend_invalid",        "Frontend move rejected by chess.js"),
        ("null_move_returned",      "AI returned null move"),
        ("training_position_error", "Training position failed to parse"),
        ("training_degraded",       "Training ran on partial game log"),
        ("gpu_detection",           "GPU detection result"),
        ("gpu_directml_fallback",   "DirectML operation fell back to CPU"),
        ("other",                   "Other"),
    ]

    timestamp   = models.DateTimeField(auto_now_add=True)
    issue_type  = models.CharField(max_length=40, choices=ISSUE_TYPES, db_index=True)
    move        = models.CharField(max_length=20, blank=True, default="")
    fen         = models.CharField(max_length=200, blank=True, default="")
    detail      = models.TextField(blank=True, default="")
    difficulty  = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        ordering = ["-timestamp"]
        indexes  = [models.Index(fields=["issue_type", "timestamp"])]

    def __str__(self):
        return f"[{self.issue_type}] {self.move or '—'} @ {self.timestamp:%Y-%m-%d %H:%M:%S}"


class OpeningMemory(models.Model):
    """Per-player opening book — moves the AI played in the first 6 full moves
    along with whether they led to wins. The AI consults this before searching
    so it builds consistent opening patterns it knows work against this player."""
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    move_number = models.IntegerField()   # fullmove number (1–6)

    fen = models.CharField(max_length=200)
    hash = models.CharField(max_length=64, db_index=True)

    move = models.CharField(max_length=10)   # UCI format

    times_used = models.IntegerField(default=1)
    wins = models.IntegerField(default=0)

    def win_rate(self):
        return self.wins / self.times_used if self.times_used > 0 else 0

    class Meta:
        indexes = [
            models.Index(fields=["player", "move_number"]),
            models.Index(fields=["hash"]),
        ]
