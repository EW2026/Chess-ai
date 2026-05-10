import chess
import chess.polyglot
import numpy as np
import torch
import torch.nn as nn

from .hardware import HW, get_app_dir

# =========================
# DEVICE + PATHS
# =========================
# DirectML (AMD/Intel DX12) uses a custom device object from torch_directml rather
# than a standard torch.device string. Fall back to CPU if the package is missing.
_hw_device = HW["device"]
if _hw_device == "dml":
    try:
        import torch_directml as _dml
        device = _dml.device()
    except ImportError:
        device = torch.device("cpu")
else:
    device = torch.device(_hw_device)
MODEL_PATH = get_app_dir() / 'chess_model.pth'

# =========================
# PIECE VALUES FOR MOVE ORDERING
# =========================
# Used by minimax.py for MVV-LVA capture ordering (Most Valuable Victim - Least Valuable Attacker).
# Values are distinct integers so the network can tell piece types apart.
# The ordering is preserved: Queen(5) > Rook(4) > Bishop(3) > Knight(2) > Pawn(1).
# King=6 won't appear as a capture victim in legal chess, but we define it for completeness.
PIECE_VALUES = {
    chess.PAWN:   1,
    chess.KNIGHT: 2,
    chess.BISHOP: 3,
    chess.ROOK:   4,
    chess.QUEEN:  5,
    chess.KING:   6,
}

# =========================
# BOARD ENCODING
# =========================
# --- CPU / small-network encoding (-6 to +6, item 2) ---
# Each square is encoded as an integer:
#   0       = empty
#   +1 to +6 = White piece (sign = White, magnitude = piece type)
#   -1 to -6 = Black piece (sign = Black, magnitude = piece type)
# Piece type mapping: Pawn=1, Knight=2, Bishop=3, Rook=4, Queen=5, King=6
# This lets the network distinguish ALL six piece types for both colors (12 classes
# instead of the old 6 that confused knight and bishop — both were value 3).

# --- GPU / large-network encoding (12 planes × 64 squares = 768, item 9) ---
# Binary planes: one plane per (color, piece_type) combination.
# Plane 0=White pawns, 1=White knights, ..., 5=White kings,
#       6=Black pawns, 7=Black knights, ..., 11=Black kings.
# Each plane is a flat 64-element array: 1.0 if that piece is on that square, 0.0 otherwise.
# This gives the network clearer spatial structure than the scalar encoding.

_PIECE_TYPE_INDEX = {
    chess.PAWN: 0, chess.KNIGHT: 1, chess.BISHOP: 2,
    chess.ROOK: 3, chess.QUEEN:  4, chess.KING:   5,
}


def board_to_tensor(board):
    """Convert a board to a torch tensor for neural network training.

    Automatically selects the encoding that matches the current network:
      - Large network (GPU, >=4GB VRAM): 768-element binary 12-plane encoding
      - Small network (CPU or low-VRAM GPU): 64-element -6 to +6 scalar encoding
    """
    if HW["use_large_net"]:
        return _board_to_tensor_large(board)
    return _board_to_tensor_small(board)


def _board_to_tensor_small(board):
    """64-element tensor, -6 to +6 encoding. For the small 64→128→64→1 network."""
    arr = np.zeros(64, dtype=np.float32)
    for sq, piece in board.piece_map().items():
        val = PIECE_VALUES[piece.piece_type]
        arr[sq] = val if piece.color == chess.WHITE else -val
    return torch.tensor(arr, device=device)


def _board_to_tensor_large(board):
    """768-element binary tensor (12 planes × 64 squares). For the large GPU network."""
    arr = np.zeros(768, dtype=np.float32)
    for sq, piece in board.piece_map().items():
        plane = _PIECE_TYPE_INDEX[piece.piece_type]
        if piece.color == chess.BLACK:
            plane += 6   # Black pieces use planes 6-11
        arr[plane * 64 + sq] = 1.0
    return torch.tensor(arr, device=device)


def _board_to_array_small(board):
    """Same as _board_to_tensor_small but returns a numpy array for fast inference."""
    arr = np.zeros(64, dtype=np.float32)
    for sq, piece in board.piece_map().items():
        val = PIECE_VALUES[piece.piece_type]
        arr[sq] = val if piece.color == chess.WHITE else -val
    return arr


def _board_to_array_large(board):
    """Same as _board_to_tensor_large but returns a numpy array for fast inference."""
    arr = np.zeros(768, dtype=np.float32)
    for sq, piece in board.piece_map().items():
        plane = _PIECE_TYPE_INDEX[piece.piece_type]
        if piece.color == chess.BLACK:
            plane += 6
        arr[plane * 64 + sq] = 1.0
    return arr


def _board_to_array(board):
    """Route to the correct numpy array encoding based on the active network."""
    if HW["use_large_net"]:
        return _board_to_array_large(board)
    return _board_to_array_small(board)


# =========================
# NEURAL NETWORK MODELS
# =========================

class ChessNetSmall(nn.Module):
    """Small 3-layer feedforward network for CPU users (or low-VRAM GPUs).

    Input: 64 floats (-6 to +6 encoding, one per square)
    Output: a single float — positive = White is better, negative = Black is better.

    Kept intentionally small because it runs thousands of times per second during
    search, so inference speed is more important than model capacity here.
    """
    def __init__(self):
        super().__init__()
        self.model = nn.Sequential(
            nn.Linear(64, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
        )

    def forward(self, x):
        return self.model(x)


class ChessNetLarge(nn.Module):
    """Larger network for GPU users with >= 4GB VRAM (item 9).

    Input: 768 floats (12-plane binary encoding, 12 × 64 squares)
    Output: a single float — positive = White is better, negative = Black is better.

    More parameters let the network learn finer positional patterns, but it's only
    practical on a GPU — CPU inference would be too slow for search use.
    """
    def __init__(self):
        super().__init__()
        self.model = nn.Sequential(
            nn.Linear(768, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Linear(128, 1),
        )

    def forward(self, x):
        return self.model(x)


# Select the right network class based on hardware
if HW["use_large_net"]:
    model = ChessNetLarge().to(device)
else:
    model = ChessNetSmall().to(device)

if MODEL_PATH.exists():
    try:
        model.load_state_dict(torch.load(str(MODEL_PATH), map_location=device))
    except Exception:
        # Weight file may be incompatible (e.g. network size changed) — start fresh.
        print("[chess-ai] Model weights incompatible with current network; starting fresh.")
model.eval()

# =========================
# NUMPY INFERENCE CACHE
# =========================
# Calling torch during the minimax search would be slow due to Python→C overhead and
# device synchronization. Instead, we extract the network weights as raw numpy arrays
# once at startup (and again after each training session), then do manual matrix
# multiplication in numpy. This is ~10-30x faster for small networks on CPU.

_weights = {}   # stores all layer weights as numpy arrays


def _refresh_eval_weights():
    """Copy the current model weights into numpy arrays for fast inference.
    Called at startup and after each training session so the search loop always
    uses the latest trained weights without torch overhead."""
    global _weights
    s = model.state_dict()
    new_weights = {}
    for key, tensor in s.items():
        new_weights[key] = tensor.detach().cpu().numpy()
    _weights = new_weights


_refresh_eval_weights()


def nn_inference(board):
    """Run the neural network on a board using the cached numpy weights.

    Does the same computation as model.forward() but entirely in numpy:
      Layer 0: Linear (matrix multiply + bias) → ReLU
      Layer 1: Linear → ReLU
      Final layer: Linear → scalar

    Returns a raw float: positive = White is better, negative = Black is better.
    This is called by evaluation.py for every leaf node in the search tree.
    """
    x = _board_to_array(board)

    if HW["use_large_net"]:
        # Large network: Linear(768→256) → ReLU → Linear(256→128) → ReLU → Linear(128→1)
        x = np.maximum(0, _weights['model.0.weight'] @ x + _weights['model.0.bias'])
        x = np.maximum(0, _weights['model.2.weight'] @ x + _weights['model.2.bias'])
        return float((_weights['model.4.weight'] @ x + _weights['model.4.bias'])[0])
    else:
        # Small network: Linear(64→128) → ReLU → Linear(128→64) → ReLU → Linear(64→1)
        x = np.maximum(0, _weights['model.0.weight'] @ x + _weights['model.0.bias'])
        x = np.maximum(0, _weights['model.2.weight'] @ x + _weights['model.2.bias'])
        return float((_weights['model.4.weight'] @ x + _weights['model.4.bias'])[0])


# =========================
# ARCHITECTURE INTROSPECTION
# =========================
def get_nn_architecture():
    """Return a human-readable architecture string, e.g. '64→128→64→1'.
    Derived from the live model so it's always accurate regardless of which
    network variant is loaded."""
    layers = []
    for m in model.modules():
        if isinstance(m, nn.Linear):
            if not layers:
                layers.append(m.in_features)
            layers.append(m.out_features)
    return "→".join(str(l) for l in layers)   # → character


def get_nn_param_count():
    """Total number of trainable parameters in the active network."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


# =========================
# HASH
# =========================
def hash_board(board):
    """Zobrist hash: a fast, collision-resistant integer fingerprint for a board position.
    Used as the transposition table key. Same position always produces the same hash."""
    return chess.polyglot.zobrist_hash(board)

def hash_board_str(board):
    return str(chess.polyglot.zobrist_hash(board))
