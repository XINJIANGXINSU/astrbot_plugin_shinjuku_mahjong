from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mahjong.constants import EAST, NORTH, SOUTH, WEST
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig, HandConstants, OptionalRules
from mahjong.meld import Meld
from mahjong.tile import TilesConverter


WINDS = {"east": EAST, "south": SOUTH, "west": WEST, "north": NORTH}
MELD_TYPES = {
    "chi": Meld.CHI,
    "pon": Meld.PON,
    "kan": Meld.KAN,
    "shouminkan": Meld.SHOUMINKAN,
}


@dataclass
class ScoreResult:
    han: int
    fu: int
    yaku: list[str]
    level: str
    payments: dict[str, int]
    total_gain: int
    hand_text: str


def _tiles(notation: str) -> list[int]:
    cleaned = re.sub(r"\s+", "", notation.lower())
    if not cleaned or not re.fullmatch(r"(?:[0-9r]+[mpsz])+", cleaned):
        raise ValueError(f"无效牌谱：{notation!r}")
    return TilesConverter.one_line_string_to_136_array(
        cleaned, has_aka_dora=True
    )


def _bool(data: dict[str, Any], key: str) -> bool:
    return bool(data.get(key, False))


def calculate_score(
    hand: dict[str, Any],
    *,
    winner_id: str,
    loser_id: str | None,
    player_ids: list[str],
    dealer_id: str,
    winner_seat: str,
    round_wind: str,
    honba: int,
    riichi_sticks: int,
) -> ScoreResult:
    closed_notation = str(hand.get("closed_tiles", ""))
    win_notation = str(hand.get("win_tile", ""))
    closed_tiles = _tiles(closed_notation)
    win_candidates = _tiles(win_notation)
    if len(win_candidates) != 1:
        raise ValueError("和牌张必须且只能有一张")

    melds: list[Meld] = []
    all_tiles = list(closed_tiles)
    for raw in hand.get("melds", []):
        if not isinstance(raw, dict):
            raise ValueError("副露格式无效")
        meld_type = MELD_TYPES.get(str(raw.get("type", "")).lower())
        if not meld_type:
            raise ValueError(f"不支持的副露类型：{raw.get('type')}")
        meld_tiles = _tiles(str(raw.get("tiles", "")))
        melds.append(
            Meld(
                meld_type=meld_type,
                tiles=meld_tiles,
                opened=bool(raw.get("opened", True)),
            )
        )
        all_tiles.extend(meld_tiles)

    win_type = win_candidates[0] // 4
    win_tile = next((tile for tile in all_tiles if tile // 4 == win_type), None)
    if win_tile is None:
        raise ValueError("和牌张不在完整手牌中")

    dora = []
    for item in hand.get("dora_indicators", []):
        dora.extend(_tiles(str(item)))
    ura = []
    for item in hand.get("ura_dora_indicators", []):
        ura.extend(_tiles(str(item)))

    is_tsumo = loser_id is None
    options = OptionalRules(
        has_open_tanyao=True,
        has_aka_dora=True,
        has_double_yakuman=True,
        kazoe_limit=HandConstants.KAZOE_LIMITED,
        kiriage=False,
        renhou_as_yakuman=False,
        limit_to_sextuple_yakuman=True,
    )
    config = HandConfig(
        is_tsumo=is_tsumo,
        is_riichi=_bool(hand, "riichi"),
        is_daburu_riichi=_bool(hand, "double_riichi"),
        is_ippatsu=_bool(hand, "ippatsu"),
        is_rinshan=_bool(hand, "rinshan"),
        is_chankan=_bool(hand, "chankan"),
        is_haitei=_bool(hand, "haitei"),
        is_houtei=_bool(hand, "houtei"),
        is_tenhou=_bool(hand, "tenhou"),
        is_chiihou=_bool(hand, "chiihou"),
        player_wind=WINDS[winner_seat],
        round_wind=WINDS.get(round_wind, EAST),
        tsumi_number=max(0, honba),
        kyoutaku_number=max(0, riichi_sticks),
        options=options,
    )
    result = HandCalculator.estimate_hand_value(
        all_tiles,
        win_tile,
        melds=melds,
        dora_indicators=dora,
        ura_dora_indicators=ura,
        config=config,
    )
    if result.error:
        raise ValueError(f"计分失败：{result.error}")
    if result.cost is None or result.han is None or result.fu is None:
        raise ValueError("计分库没有返回完整结果")

    payments = {player_id: 0 for player_id in player_ids}
    cost = result.cost
    if loser_id:
        paid = cost["main"] + cost["main_bonus"]
        payments[loser_id] -= paid
        payments[winner_id] += paid + cost["kyoutaku_bonus"]
    else:
        winner_is_dealer = winner_seat == "east"
        for player_id in player_ids:
            if player_id == winner_id:
                continue
            if winner_is_dealer or player_id == dealer_id:
                paid = cost["main"] + cost["main_bonus"]
            else:
                paid = cost["additional"] + cost["additional_bonus"]
            payments[player_id] -= paid
            payments[winner_id] += paid
        payments[winner_id] += cost["kyoutaku_bonus"]

    yaku = [str(item) for item in (result.yaku or [])]
    return ScoreResult(
        han=result.han,
        fu=result.fu,
        yaku=yaku,
        level=cost.get("yaku_level", ""),
        payments=payments,
        total_gain=payments[winner_id],
        hand_text=closed_notation,
    )
