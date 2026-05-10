from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase
from unittest.mock import patch
import chess

from api.models import IssueLog, Player


# ── Helpers ────────────────────────────────────────────────────────────────────

START_FEN  = chess.Board().fen()
# Fool's mate — white is checkmated, zero legal moves
MATE_FEN   = 'rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3'


def make_token_client(client):
    """Create (or reuse) the 'local' user/token and attach credentials to client."""
    user, _ = User.objects.get_or_create(username='local')
    token, _ = Token.objects.get_or_create(user=user)
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return token


# ── Health ─────────────────────────────────────────────────────────────────────

class HealthTest(APITestCase):
    def test_returns_ok(self):
        r = self.client.get('/api/health/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'status': 'ok'})

    def test_no_auth_required(self):
        r = self.client.get('/api/health/')
        self.assertNotEqual(r.status_code, 401)


# ── Local token ────────────────────────────────────────────────────────────────

class LocalTokenTest(APITestCase):
    def test_returns_token_key(self):
        r = self.client.get('/api/local-token/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('token', r.json())

    def test_token_has_bearer_prefix(self):
        r = self.client.get('/api/local-token/')
        self.assertTrue(r.json()['token'].startswith('Token '))

    def test_repeated_calls_return_same_token(self):
        t1 = self.client.get('/api/local-token/').json()['token']
        t2 = self.client.get('/api/local-token/').json()['token']
        self.assertEqual(t1, t2)


# ── Auth guard ─────────────────────────────────────────────────────────────────

class AuthGuardTest(APITestCase):
    def test_ai_move_no_auth_is_401(self):
        r = self.client.post('/api/ai-move/', {'fen': START_FEN}, format='json')
        self.assertEqual(r.status_code, 401)

    def test_ai_move_bad_token_is_401(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token not-a-real-token')
        r = self.client.post('/api/ai-move/', {'fen': START_FEN}, format='json')
        self.assertEqual(r.status_code, 401)

    def test_train_no_auth_is_401(self):
        r = self.client.post('/api/train/', {'game_log': [], 'won': False}, format='json')
        self.assertEqual(r.status_code, 401)

    def test_train_bad_token_is_401(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token not-a-real-token')
        r = self.client.post('/api/train/', {'game_log': [], 'won': False}, format='json')
        self.assertEqual(r.status_code, 401)

    def test_public_endpoints_accessible_without_auth(self):
        cases = [
            ('get',  '/api/health/'),
            ('get',  '/api/local-token/'),
            ('get',  '/api/player-stats/'),
            ('post', '/api/new-game/'),
            ('post', '/api/warmup-pool/'),
            ('get',  '/api/issue-log/'),
        ]
        for method, url in cases:
            with self.subTest(url=url):
                r = getattr(self.client, method)(url)
                self.assertNotEqual(r.status_code, 401, msg=f'{url} should not require auth')


# ── New game ───────────────────────────────────────────────────────────────────

class NewGameTest(APITestCase):
    @patch('api.views.reset_tt')
    @patch('api.views.stop_ponder')
    def test_returns_ok(self, _stop, _reset):
        r = self.client.post('/api/new-game/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'ok')

    @patch('api.views.reset_tt')
    @patch('api.views.stop_ponder')
    def test_repeated_calls_succeed(self, _stop, _reset):
        r1 = self.client.post('/api/new-game/')
        r2 = self.client.post('/api/new-game/')
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)


# ── Warmup pool ────────────────────────────────────────────────────────────────

class WarmupPoolTest(APITestCase):
    def test_returns_warming(self):
        r = self.client.post('/api/warmup-pool/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'warming')


# ── Player stats ───────────────────────────────────────────────────────────────

class PlayerStatsTest(APITestCase):
    def test_unknown_player_defaults_to_1200(self):
        r = self.client.get('/api/player-stats/?player=Nobody')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['elo'], 1200)

    def test_response_has_name_and_elo(self):
        r = self.client.get('/api/player-stats/')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn('name', data)
        self.assertIn('elo', data)

    def test_known_player_returns_stored_elo(self):
        Player.objects.create(name='Magnus', elo=2850)
        r = self.client.get('/api/player-stats/?player=Magnus')
        self.assertEqual(r.json()['elo'], 2850)


# ── AI move ────────────────────────────────────────────────────────────────────

class AIMoveTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        user, _ = User.objects.get_or_create(username='local')
        cls.token, _ = Token.objects.get_or_create(user=user)

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

    @patch('api.views.get_ai_move')
    def test_response_shape(self, mock_ai):
        mock_ai.return_value = chess.Move.from_uci('e2e4')
        r = self.client.post('/api/ai-move/', {
            'fen': START_FEN, 'moves': [], 'difficulty': 'easy', 'player': 'Player1',
        }, format='json')
        self.assertEqual(r.status_code, 200)
        move = r.json()['move']
        self.assertIsNotNone(move)
        for field in ('uci', 'from', 'to', 'san', 'is_capture', 'is_check'):
            self.assertIn(field, move, msg=f'missing field: {field}')

    @patch('api.views.get_ai_move')
    def test_returned_move_is_legal(self, mock_ai):
        mock_ai.return_value = chess.Move.from_uci('e2e4')
        board = chess.Board()
        r = self.client.post('/api/ai-move/', {
            'fen': board.fen(), 'moves': [], 'difficulty': 'easy', 'player': 'Player1',
        }, format='json')
        uci = r.json()['move']['uci']
        self.assertIn(chess.Move.from_uci(uci), board.legal_moves)

    @patch('api.views.get_ai_move')
    def test_checkmate_position_returns_null_move(self, mock_ai):
        mock_ai.return_value = None
        r = self.client.post('/api/ai-move/', {
            'fen': MATE_FEN, 'moves': [], 'difficulty': 'easy', 'player': 'Player1',
        }, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()['move'])

    @patch('api.views.get_ai_move')
    def test_illegal_engine_move_is_blocked(self, mock_ai):
        # The view must not relay a move the engine claims but that chess.js would reject.
        # h1h2 is not legal from the starting position.
        mock_ai.return_value = chess.Move.from_uci('h1h2')
        r = self.client.post('/api/ai-move/', {
            'fen': START_FEN, 'moves': [], 'difficulty': 'easy', 'player': 'Player1',
        }, format='json')
        self.assertIsNone(r.json()['move'])

    @patch('api.views.get_ai_move')
    def test_move_history_overrides_fen(self, mock_ai):
        # When 'moves' is supplied the view replays the full history to reconstruct
        # the board, so the board passed to the engine reflects the move list.
        mock_ai.return_value = chess.Move.from_uci('d2d4')
        moves = ['e2e4', 'e7e5']
        board = chess.Board()
        for m in moves:
            board.push(chess.Move.from_uci(m))
        r = self.client.post('/api/ai-move/', {
            'fen': board.fen(), 'moves': moves, 'difficulty': 'medium', 'player': 'Player1',
        }, format='json')
        self.assertEqual(r.status_code, 200)
        # The board handed to get_ai_move should match the replayed position
        called_board = mock_ai.call_args[0][0]
        self.assertEqual(called_board.fen().split()[:4], board.fen().split()[:4])

    @patch('api.views.analyze_player_move')
    @patch('api.views.get_ai_move')
    def test_easy_mode_returns_analysis_field(self, mock_ai, mock_analyze):
        mock_ai.return_value = chess.Move.from_uci('e2e4')
        mock_analyze.return_value = {
            'mistake': True, 'best_move': 'e2e4', 'best_move_san': 'e4',
            'reason': 'Better center', 'eval_diff': 0.5,
        }
        r = self.client.post('/api/ai-move/', {
            'fen': START_FEN, 'moves': [], 'difficulty': 'easy', 'player': 'Player1',
            'prev_fen': START_FEN, 'player_move': {'from': 'e2', 'to': 'e4'},
        }, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertIn('analysis', r.json())

    def test_missing_auth_returns_401(self):
        self.client.credentials()
        r = self.client.post('/api/ai-move/', {'fen': START_FEN, 'difficulty': 'easy'}, format='json')
        self.assertEqual(r.status_code, 401)


# ── Train ──────────────────────────────────────────────────────────────────────

class TrainTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        user, _ = User.objects.get_or_create(username='local')
        cls.token, _ = Token.objects.get_or_create(user=user)

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

    @patch('api.views.train_model')
    def test_returns_player_elo(self, _train):
        r = self.client.post('/api/train/', {
            'game_log': [], 'won': False, 'player': 'Player1', 'ai_color': 'b',
        }, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertIn('player_elo', r.json())

    @patch('api.views.train_model')
    def test_player_elo_is_integer(self, _train):
        r = self.client.post('/api/train/', {
            'game_log': [], 'won': False, 'player': 'Player1', 'ai_color': 'b',
        }, format='json')
        self.assertIsInstance(r.json()['player_elo'], int)

    @patch('api.views.train_model')
    def test_elo_decreases_after_loss(self, _train):
        # won=True means the AI won, so the player lost — ELO should drop below 1200
        r = self.client.post('/api/train/', {
            'game_log': [], 'won': True, 'player': 'Player1', 'ai_color': 'b',
        }, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertLess(r.json()['player_elo'], 1200)

    @patch('api.views.train_model')
    def test_elo_increases_after_win(self, _train):
        # won=False means the player won — ELO should rise above 1200
        r = self.client.post('/api/train/', {
            'game_log': [], 'won': False, 'player': 'Player1', 'ai_color': 'b',
        }, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json()['player_elo'], 1200)

    def test_missing_auth_returns_401(self):
        self.client.credentials()
        r = self.client.post('/api/train/', {'game_log': [], 'won': False}, format='json')
        self.assertEqual(r.status_code, 401)


# ── Issue log ──────────────────────────────────────────────────────────────────

class IssueLogTest(APITestCase):
    def test_returns_list(self):
        r = self.client.get('/api/issue-log/')
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_response_shape(self):
        IssueLog.objects.create(issue_type='other', detail='test entry')
        r = self.client.get('/api/issue-log/')
        entry = r.json()[0]
        for field in ('id', 'timestamp', 'issue_type', 'move', 'fen', 'detail', 'difficulty'):
            self.assertIn(field, entry, msg=f'missing field: {field}')

    def test_limit_parameter(self):
        for i in range(10):
            IssueLog.objects.create(issue_type='other', detail=f'entry {i}')
        r = self.client.get('/api/issue-log/?limit=4')
        self.assertLessEqual(len(r.json()), 4)

    def test_type_filter(self):
        IssueLog.objects.create(issue_type='board_mismatch', detail='mismatch entry')
        IssueLog.objects.create(issue_type='other', detail='unrelated entry')
        r = self.client.get('/api/issue-log/?type=board_mismatch')
        data = r.json()
        self.assertTrue(len(data) >= 1)
        self.assertTrue(all(e['issue_type'] == 'board_mismatch' for e in data))
