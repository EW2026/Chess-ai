// Special-move tutorials. Each entry uses the same shape as openings.js EXCEPT:
//
//   startFen: object keyed by color instead of a single string.
//             The starting position is fundamentally different depending on which
//             side you play (e.g. en passant as White requires a different board
//             than en passant as Black), so we store one FEN per color.
//
//   moves:    object keyed by color instead of a flat array.
//             Each color gets its own move sequence because the pieces involved,
//             directions of movement, and explanations are all different per side.
//
// Tutorial.jsx's startOpening() detects these object shapes and resolves the
// correct FEN and moves array for the chosen color before starting the lesson.

const SPECIAL_MOVES = [
  {
    key: "kingside_castling",
    name: "Kingside Castling",
    description: "Move your king to safety — both the king and the rook move in a single turn.",
    category: "special",
    defaultColor: "w",
    startFen: {
      // Both kings and both rooks present, all other squares clear.
      // The active color in the FEN matches the chosen side so the right player moves first.
      // "K" in the castling rights field means White can castle kingside; "k" means Black can.
      w: "r3k2r/8/8/8/8/8/8/4K2R w K - 0 1",
      b: "r3k2r/8/8/8/8/8/8/4K2R b k - 0 1",
    },
    moves: {
      w: [
        {
          side: "w", from: "e1", to: "g1",
          explanation: "Kingside castling! The king moves two squares to g1 and the rook on h1 hops over to f1 — both pieces move in one turn. You can castle only when: (1) neither piece has moved before, (2) no pieces stand between them, (3) the king is not in check, and (4) the king does not pass through an attacked square.",
        },
      ],
      b: [
        {
          side: "b", from: "e8", to: "g8",
          explanation: "Kingside castling as Black! The king moves two squares to g8 and the rook on h8 hops over to f8 — both pieces move in one turn. The same rules apply: neither piece can have moved before, and the king cannot castle through or into check.",
        },
      ],
    },
  },

  {
    key: "queenside_castling",
    name: "Queenside Castling",
    description: "Castle toward the queenside rook — the king moves two squares left and the rook jumps over it.",
    category: "special",
    defaultColor: "w",
    startFen: {
      // "Q" = White can castle queenside; "q" = Black can castle queenside.
      // Both rooks are present so the student can see the queenside rook jump to d1/d8.
      w: "r3k2r/8/8/8/8/8/8/R3K2R w Q - 0 1",
      b: "r3k2r/8/8/8/8/8/8/R3K2R b q - 0 1",
    },
    moves: {
      w: [
        {
          side: "w", from: "e1", to: "c1",
          explanation: "Queenside castling! The king moves two squares left to c1, and the rook on a1 jumps over to d1. Queenside castling requires b1, c1, and d1 to all be empty. The king must not pass through or land on an attacked square, same as kingside castling.",
        },
      ],
      b: [
        {
          side: "b", from: "e8", to: "c8",
          explanation: "Queenside castling as Black! The king moves to c8 and the rook on a8 hops to d8. b8, c8, and d8 must all be empty, and the king still cannot castle through or into check.",
        },
      ],
    },
  },

  {
    key: "en_passant",
    name: "En Passant",
    description: "Capture a pawn that just advanced two squares past the square you could have taken it on.",
    category: "special",
    defaultColor: "w",
    startFen: {
      // White perspective: the opponent pawn (d7) hasn't moved yet, so it's Black's turn first.
      // The lesson shows Black's double-advance and then asks White to capture en passant.
      w: "4k3/3p4/8/4P3/8/8/8/4K3 b - - 0 1",
      // Black perspective: the opponent pawn (d2) hasn't moved yet, so it's White's turn first.
      b: "4k3/8/8/8/4p3/8/3P4/4K3 w - - 0 1",
    },
    moves: {
      w: [
        {
          // Opponent's move (side: "b") plays automatically, demonstrating the double-pawn advance
          side: "b", from: "d7", to: "d5",
          explanation: "Black's pawn advances two squares, trying to sneak past your pawn on e5. Normally the capture chance would be gone — but en passant gives you one move to take it.",
        },
        {
          side: "w", from: "e5", to: "d6",
          explanation: "En passant! Move your pawn diagonally to d6 — Black's pawn on d5 is removed from the board even though you didn't land on d5. This capture is only legal on the very next move after the opponent's pawn advances two squares. Wait even one move and the opportunity is gone forever.",
        },
      ],
      b: [
        {
          side: "w", from: "d2", to: "d4",
          explanation: "White's pawn rushes forward two squares past d3, the square where you could have captured it. En passant lets you take it anyway — but you must act immediately.",
        },
        {
          side: "b", from: "e4", to: "d3",
          explanation: "En passant as Black! Your pawn on e4 captures diagonally to d3, removing White's pawn from d4. The captured pawn disappears from d4 even though your pawn lands on d3. This rule exists only for pawns, only after a two-square advance, and only on the very next turn.",
        },
      ],
    },
  },

  {
    key: "pawn_promotion",
    name: "Pawn Promotion",
    description: "Advance a pawn all the way to the last rank and turn it into any piece you choose.",
    category: "special",
    defaultColor: "w",
    startFen: {
      // White pawn on e7, one step from promotion. Black king placed far away so nothing stops it.
      w: "3k4/4P3/8/8/8/8/8/4K3 w - - 0 1",
      // Black pawn on e2, one step from promotion on e1.
      b: "3K4/8/8/8/8/3k4/4p3/8 b - - 0 1",
    },
    moves: {
      w: [
        {
          side: "w", from: "e7", to: "e8",
          explanation: "Promotion! When a pawn reaches the back rank it must immediately be replaced by a queen, rook, bishop, or knight of the same color. Almost always choose a queen — it is the strongest piece on the board. Promoting a passed pawn is often the decisive moment in an endgame.",
        },
      ],
      b: [
        {
          side: "b", from: "e2", to: "e1",
          explanation: "Promotion as Black! Your pawn reaches rank 1 and must be replaced. Choose your new piece — a queen wins almost every endgame. The same rules apply in reverse: reach the first rank and promote.",
        },
      ],
    },
  },
];

export default SPECIAL_MOVES;
