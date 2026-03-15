# -*- coding: utf-8 -*-

import unittest

from src.chat.features.games.services.blackjack_game import (
    BlackjackGame,
    Card,
    GameState,
    GameResult,
)


def _build_insurance_game() -> BlackjackGame:
    game = BlackjackGame(player_id=123456)
    filler_cards = [Card("♠️", "9") for _ in range(10)]
    rigged_tail = [
        Card("♣️", "A"),
        Card("♠️", "Q"),
        Card("♦️", "2"),
        Card("♥️", "8"),
    ]
    game.deck.cards = filler_cards + rigged_tail
    return game


class TestBlackjackInsuranceUI(unittest.TestCase):
    def test_start_game_with_dealer_ace_enters_insurance_state(self):
        game = _build_insurance_game()

        success, _ = game.start_game(70)

        self.assertTrue(success)
        self.assertEqual(game.state, GameState.WAITING_INSURANCE)
        self.assertTrue(game.insurance_available)

    def test_skip_insurance_returns_to_player_turn(self):
        game = _build_insurance_game()
        game.start_game(70)

        success, _ = game.skip_insurance()

        self.assertTrue(success)
        self.assertEqual(game.state, GameState.PLAYER_TURN)
        self.assertIsNone(game.result)
        self.assertEqual(game.payout, 0)

    def test_cannot_stand_before_insurance_decision(self):
        game = _build_insurance_game()
        game.start_game(70)

        success, _ = game.player_stand()

        self.assertFalse(success)
        self.assertEqual(game.state, GameState.WAITING_INSURANCE)
        self.assertIsNone(game.result)
        self.assertEqual(game.payout, 0)

    def test_buy_insurance_pays_when_dealer_has_blackjack(self):
        game = _build_insurance_game()
        game.deck.cards = [Card("♠️", "9") for _ in range(10)] + [
            Card("♣️", "A"),
            Card("♠️", "Q"),
            Card("♦️", "K"),
            Card("♥️", "8"),
        ]

        game.start_game(70)
        success, _ = game.buy_insurance()

        self.assertTrue(success)
        self.assertEqual(game.state, GameState.FINISHED)
        self.assertEqual(game.result, GameResult.DEALER_WIN)
        self.assertEqual(game.insurance_bet, 35)
        self.assertEqual(game.insurance_payout, 105)
        self.assertEqual(game.payout, 0)
