const PIECE_MOVEMENTS = [
  {
    key: "pawn",
    name: "Pawn",
    symbol: "♙",
    description: "Advances forward one square, with a special two-square option from its starting rank. Captures diagonally.",
    slides: [
      {
        type: "explain",
        title: "Starting Rank: Two Options",
        fen: "7k/8/8/8/8/8/4P3/7K w - - 0 1",
        activeSquare: "e2",
        highlights: ["e3", "e4"],
        explanation:
          "From its starting rank (rank 2 for White, rank 7 for Black), a pawn may advance one or two squares forward on its very first move. This two-square option disappears once the pawn has moved.",
      },
      {
        type: "explain",
        title: "After Moving: One Square Only",
        fen: "7k/8/8/4P3/8/8/8/7K w - - 0 1",
        activeSquare: "e5",
        highlights: ["e6"],
        explanation:
          "Once a pawn has left its starting rank it can only advance one square at a time — no more two-square jumps. It marches steadily forward, one step per turn.",
      },
      {
        type: "explain",
        title: "Diagonal Captures",
        fen: "7k/8/8/3p1p2/4P3/8/8/7K w - - 0 1",
        activeSquare: "e4",
        highlights: ["e5", "d5", "f5"],
        explanation:
          "Pawns capture one square diagonally forward. They cannot capture straight ahead and can only land diagonally on a square occupied by an enemy piece. Here the pawn can advance to e5 or capture the black pawns on d5 and f5.",
      },
      {
        type: "practice",
        title: "Your Turn",
        fen: "7k/8/8/3p1p2/4P3/8/8/7K w - - 0 1",
        activeSquare: "e4",
        instruction:
          "Move the pawn to any valid square — advance forward or capture diagonally.",
      },
    ],
  },

  {
    key: "knight",
    name: "Knight",
    symbol: "♘",
    description: "Moves in an L-shape and is the only piece that can jump over other pieces.",
    slides: [
      {
        type: "explain",
        title: "The L-Shape Move",
        fen: "7k/8/8/8/3N4/8/8/7K w - - 0 1",
        activeSquare: "d4",
        highlights: ["c2", "b3", "b5", "c6", "e6", "f5", "f3", "e2"],
        explanation:
          "The knight moves in an L-shape: two squares in one direction and one square perpendicular (or one square then two). From the center of the board it can reach up to 8 different squares — more than any other piece in its immediate vicinity.",
      },
      {
        type: "explain",
        title: "Jumping Over Pieces",
        fen: "7k/8/8/2ppp3/2pNp3/2ppp3/8/7K w - - 0 1",
        activeSquare: "d4",
        highlights: ["c2", "b3", "b5", "c6", "e6", "f5", "f3", "e2"],
        explanation:
          "The knight is the only piece that jumps — it teleports directly to its destination, completely ignoring every piece in between. All those enemy pawns crowding around d4 on c3, d3, e3, c4, e4, c5, d5, e5 are irrelevant. The knight still reaches the same 8 squares. Compare this to the queen or bishop, which would be completely trapped here.",
      },
      {
        type: "practice",
        title: "Your Turn",
        fen: "7k/8/8/2ppp3/2pNp3/2ppp3/8/7K w - - 0 1",
        activeSquare: "d4",
        instruction:
          "Move the knight to any valid square — it jumps straight over all those surrounding pawns.",
      },
    ],
  },

  {
    key: "bishop",
    name: "Bishop",
    symbol: "♗",
    description: "Slides diagonally any number of squares, but is blocked by pieces in its path.",
    slides: [
      {
        type: "explain",
        title: "Diagonal Movement",
        fen: "7k/8/8/8/3B4/8/8/7K w - - 0 1",
        activeSquare: "d4",
        highlights: ["e5", "f6", "g7", "c5", "b6", "a7", "e3", "f2", "g1", "c3", "b2", "a1"],
        explanation:
          "The bishop slides any number of squares diagonally. It controls all four diagonal directions from its current square. Notice it always stays on the same color — a bishop starting on a light square can never reach a dark square.",
      },
      {
        type: "explain",
        title: "Blocked by Pieces",
        fen: "7k/8/1p3p2/8/3B4/4P3/1P6/7K w - - 0 1",
        activeSquare: "d4",
        highlights: ["e5", "f6", "c5", "b6", "c3"],
        explanation:
          "Unlike the knight, the bishop cannot jump over pieces. It slides until it hits something. It can capture an enemy piece (like the black pawns on f6 and b6), landing on that square, but cannot pass through it. A friendly piece (like the white pawns on e3 and b2) blocks the diagonal entirely — the bishop cannot land there at all. Compare this to the knight in the same surrounded position: the knight would jump straight through.",
      },
      {
        type: "practice",
        title: "Your Turn",
        fen: "7k/8/1p3p2/8/3B4/4P3/1P6/7K w - - 0 1",
        activeSquare: "d4",
        instruction:
          "Move the bishop to any valid square — remember it stops when it hits a piece.",
      },
    ],
  },

  {
    key: "rook",
    name: "Rook",
    symbol: "♖",
    description: "Slides horizontally or vertically any number of squares, but is blocked by pieces in its path.",
    slides: [
      {
        type: "explain",
        title: "Ranks and Files",
        fen: "7k/8/8/8/3R4/8/8/7K w - - 0 1",
        activeSquare: "d4",
        highlights: [
          "d5", "d6", "d7", "d8",
          "d3", "d2", "d1",
          "e4", "f4", "g4", "h4",
          "c4", "b4", "a4",
        ],
        explanation:
          "The rook slides any number of squares horizontally or vertically — along ranks (rows) and files (columns). From the center it controls an entire cross of squares and can cover enormous distances in one move.",
      },
      {
        type: "explain",
        title: "Blocked by Pieces",
        fen: "7k/8/8/3p4/1P1R2p1/8/8/7K w - - 0 1",
        activeSquare: "d4",
        highlights: ["d5", "d3", "d2", "d1", "e4", "f4", "g4", "c4"],
        explanation:
          "Like the bishop, the rook cannot jump over pieces. It slides until it hits something. It can capture the black pawns on d5 and g4 (landing on that square and stopping), but the white pawn on b4 blocks it from reaching b4 or a4 — friendly pieces are impassable walls. This is in direct contrast to the knight, which ignores everything in between.",
      },
      {
        type: "practice",
        title: "Your Turn",
        fen: "7k/8/8/3p4/1P1R2p1/8/8/7K w - - 0 1",
        activeSquare: "d4",
        instruction:
          "Move the rook to any valid square — it stops when it meets a piece.",
      },
    ],
  },

  {
    key: "queen",
    name: "Queen",
    symbol: "♛",
    description: "The most powerful piece — combines the rook and bishop, sliding in all 8 directions.",
    slides: [
      {
        type: "explain",
        title: "All 8 Directions",
        fen: "7k/8/8/8/3Q4/8/8/7K w - - 0 1",
        activeSquare: "d4",
        highlights: [
          "d5", "d6", "d7", "d8",
          "d3", "d2", "d1",
          "e4", "f4", "g4", "h4",
          "c4", "b4", "a4",
          "e5", "f6", "g7",
          "c5", "b6", "a7",
          "e3", "f2", "g1",
          "c3", "b2", "a1",
        ],
        explanation:
          "The queen combines the rook and the bishop — it slides any number of squares horizontally, vertically, or diagonally. From the center it controls more squares than any other piece, making it the most powerful piece on the board.",
      },
      {
        type: "explain",
        title: "Blocked — Contrast with the Knight",
        fen: "7k/8/8/2ppp3/2pQp3/2ppp3/8/7K w - - 0 1",
        activeSquare: "d4",
        highlights: ["c3", "d3", "e3", "c4", "e4", "c5", "d5", "e5"],
        explanation:
          "Put the queen in the same surrounded position as the knight from the previous lesson. The queen can only reach the 8 immediately adjacent pawns — it cannot get past them in any direction. The knight in this exact same position would jump straight to 8 far squares, completely ignoring those pieces. Sliding pieces (queen, rook, bishop) are stopped by anything in their path; the knight is not.",
      },
      {
        type: "explain",
        title: "Partially Blocked",
        fen: "7k/8/8/3p4/2pQp3/8/8/7K w - - 0 1",
        activeSquare: "d4",
        highlights: [
          "d5",
          "d3", "d2", "d1",
          "e4",
          "c4",
          "e5", "f6", "g7",
          "c5", "b6", "a7",
          "e3", "f2", "g1",
          "c3", "b2", "a1",
        ],
        explanation:
          "When only some directions are blocked, the queen's reach is selectively limited. Here the black pawns on c4, e4, and d5 each stop one line early — the queen can capture them but cannot continue past. All other directions remain open. Notice how selectively placing even a few pieces dramatically shrinks the queen's control.",
      },
      {
        type: "practice",
        title: "Your Turn",
        fen: "7k/8/8/3p4/2pQp3/8/8/7K w - - 0 1",
        activeSquare: "d4",
        instruction:
          "Move the queen to any valid square — it slides until it captures or hits empty space.",
      },
    ],
  },

  {
    key: "king",
    name: "King",
    symbol: "♚",
    description: "Moves exactly one square in any direction. Must never move into an attacked square.",
    slides: [
      {
        type: "explain",
        title: "One Square in Any Direction",
        fen: "7k/8/8/8/3K4/8/8/8 w - - 0 1",
        activeSquare: "d4",
        highlights: ["c3", "d3", "e3", "c4", "e4", "c5", "d5", "e5"],
        explanation:
          "The king can move exactly one square in any of the 8 directions — horizontally, vertically, or diagonally. Unlike the queen it can only take one step at a time, which makes it slow but still capable of reaching any square on the board given enough moves.",
      },
      {
        type: "explain",
        title: "Edge and Corner Penalty",
        fen: "7k/8/8/8/8/8/8/K7 w - - 0 1",
        activeSquare: "a1",
        highlights: ["a2", "b1", "b2"],
        explanation:
          "In the corner the king has only 3 squares available instead of 8. On the edge it has at most 5. This is why being pushed to the corner or edge is dangerous in an endgame — fewer escape routes means it is much easier to deliver checkmate.",
      },
      {
        type: "practice",
        title: "Your Turn",
        fen: "7k/8/8/8/3K4/8/8/8 w - - 0 1",
        activeSquare: "d4",
        instruction:
          "Move the king to any adjacent square — exactly one step in any direction.",
      },
    ],
  },
];

export default PIECE_MOVEMENTS;
