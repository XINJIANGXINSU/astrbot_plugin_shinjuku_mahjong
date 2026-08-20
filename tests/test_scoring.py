from scoring import calculate_score


HAND = {
    "closed_tiles": "22444m333567p444s",
    "win_tile": "4s",
    "melds": [],
    "dora_indicators": [],
    "ura_dora_indicators": [],
}


def test_non_dealer_ron():
    result = calculate_score(
        HAND,
        winner_id="south",
        loser_id="west",
        player_ids=["east", "south", "west", "north"],
        dealer_id="east",
        winner_seat="south",
        round_wind="east",
        honba=0,
        riichi_sticks=0,
    )
    assert (result.han, result.fu) == (1, 40)
    assert result.payments == {
        "east": 0,
        "south": 1300,
        "west": -1300,
        "north": 0,
    }


def test_non_dealer_tsumo():
    result = calculate_score(
        HAND,
        winner_id="south",
        loser_id=None,
        player_ids=["east", "south", "west", "north"],
        dealer_id="east",
        winner_seat="south",
        round_wind="east",
        honba=0,
        riichi_sticks=0,
    )
    assert (result.han, result.fu) == (4, 40)
    assert result.payments == {
        "east": -4000,
        "south": 8000,
        "west": -2000,
        "north": -2000,
    }


def test_open_hand_with_pon():
    hand = {
        "closed_tiles": "22m333567p444s",
        "win_tile": "4s",
        "melds": [{"type": "pon", "tiles": "444m", "opened": True}],
        "dora_indicators": [],
        "ura_dora_indicators": [],
    }
    result = calculate_score(
        hand,
        winner_id="south",
        loser_id="west",
        player_ids=["east", "south", "west", "north"],
        dealer_id="east",
        winner_seat="south",
        round_wind="east",
        honba=0,
        riichi_sticks=0,
    )
    assert (result.han, result.fu) == (1, 30)
    assert result.payments["south"] == 1000
    assert result.payments["west"] == -1000

