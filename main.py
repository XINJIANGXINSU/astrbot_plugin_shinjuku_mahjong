from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .rank_db import RankDatabase
from .scoring import calculate_score
from .storage import Player, Table, TableStore
from .vision import VISION_SYSTEM_PROMPT, build_prompt, parse_json_response


PLUGIN_NAME = "astrbot_plugin_shinjuku_mahjong"
SEATS = ("east", "south", "west", "north")
SEAT_NAMES = {"east": "东", "south": "南", "west": "西", "north": "北"}


@register(PLUGIN_NAME, "li", "新宿日麻", "0.1.1")
class ShinjukuMahjongPlugin(Star):
    """新宿日麻：整桌图片识别、雀魂规则算分与四人牌局记账。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.store = TableStore(data_dir / "tables.json")
        self.use_shinjuku_user_database = bool(
            self.config.get("use_shinjuku_user_database", True)
        )
        if self.use_shinjuku_user_database:
            configured_path = str(self.config.get("shinjuku_database_path", "") or "")
            rank_db_path = (
                StarTools.get_data_dir("astrbot_plugin_shinjuku") / "shinjuku.db"
                if not configured_path
                else Path(configured_path)
            )
        else:
            rank_db_path = data_dir / "mahjong_rank.db"
        self.rank_db = RankDatabase(rank_db_path, self.use_shinjuku_user_database)
        self._lock = asyncio.Lock()

    def _has_trigger(self, event: AstrMessageEvent) -> bool:
        text = event.get_message_str().strip()
        if re.search(r"(?:^|\s)麻(?:\s|$)", text):
            return True
        return bool(re.fullmatch(r"麻(?:确认|取消|查询|状态|撤销|结束|帮助)", text))

    def _session_id(self, event: AstrMessageEvent) -> str:
        group_id = event.get_group_id()
        if group_id:
            return f"group:{group_id}"
        return str(event.unified_msg_origin)

    def _mentions(self, event: AstrMessageEvent) -> list[tuple[str, str]]:
        try:
            self_id = str(event.get_self_id())
        except Exception:
            self_id = ""
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for component in event.get_messages():
            if type(component).__name__.lower() not in {"at", "mention"}:
                continue
            user_id = str(getattr(component, "qq", "") or "")
            if not user_id or user_id == "all" or user_id == self_id or user_id in seen:
                continue
            name = str(getattr(component, "name", "") or user_id)
            result.append((user_id, name))
            seen.add(user_id)
        return result

    def _image_urls(self, event: AstrMessageEvent) -> list[str]:
        result: list[str] = []

        def visit(components: list[Any]) -> None:
            for component in components:
                kind = type(component).__name__.lower()
                if kind == "image":
                    value = (
                        getattr(component, "url", None)
                        or getattr(component, "path", None)
                        or getattr(component, "file", None)
                    )
                    if value and str(value) not in result:
                        result.append(str(value))
                elif kind == "reply":
                    chain = getattr(component, "chain", None)
                    if isinstance(chain, list):
                        visit(chain)

        visit(event.get_messages())
        return result

    @staticmethod
    def _player_label(player: Player) -> str:
        """优先显示群昵称；昵称获取失败时回退到平台用户 ID。"""
        name = str(player.name or "").strip()
        if name and name.lower() not in {"none", "null"} and name != player.user_id:
            return name
        return player.user_id

    def _table_text(self, table: Table) -> str:
        lines = [
            f"当前牌桌：{SEAT_NAMES.get(table.round_wind, '东')}{table.round_number}局 "
            f"{table.honba}本场，立直棒{table.riichi_sticks}"
        ]
        for player in table.players:
            current_seat = self._current_seat(table, player.user_id)
            lines.append(
                f"{SEAT_NAMES[current_seat]}家 {self._player_label(player)}：{player.points}点"
            )
        if table.pending:
            lines.append("当前有一条待确认的和牌记录。")
        return "\n".join(lines)

    def _parse_round(self, text: str, table: Table) -> None:
        match = re.search(r"([东南西])\s*([1-4])\s*局", text)
        if match:
            table.round_wind = {"东": "east", "南": "south", "西": "west"}[match.group(1)]
            table.round_number = int(match.group(2))
        honba = re.search(r"(\d+)\s*本场", text)
        if honba:
            table.honba = int(honba.group(1))

    def _current_seat(self, table: Table, user_id: str) -> str:
        player_index = next(
            index for index, player in enumerate(table.players) if player.user_id == user_id
        )
        dealer_index = (table.round_number - 1) % 4
        return SEATS[(player_index - dealer_index) % 4]

    def _current_dealer_id(self, table: Table) -> str:
        return table.players[(table.round_number - 1) % 4].user_id

    def _apply_text_conditions(self, hand: dict[str, Any], text: str) -> None:
        flags = {
            "double_riichi": ("双立直", "两立直"),
            "riichi": ("立直",),
            "ippatsu": ("一发",),
            "rinshan": ("岭上", "嶺上"),
            "chankan": ("抢杠", "搶槓"),
            "haitei": ("海底",),
            "houtei": ("河底",),
            "tenhou": ("天和",),
            "chiihou": ("地和",),
        }
        for key, words in flags.items():
            if any(word in text for word in words):
                hand[key] = True
        if hand.get("double_riichi"):
            hand["riichi"] = False

    async def _recognize(self, event: AstrMessageEvent, images: list[str], result_type: str) -> dict[str, Any]:
        provider_id = await self.context.get_current_chat_provider_id(
            event.unified_msg_origin
        )
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=build_prompt(event.get_message_str(), result_type),
            image_urls=images,
            system_prompt=VISION_SYSTEM_PROMPT,
            temperature=0,
        )
        return parse_json_response(response.completion_text)

    async def _create_table(self, event: AstrMessageEvent) -> str:
        mentions = self._mentions(event)
        if len(mentions) != 4:
            return "开桌需要按东、南、西、北顺序艾特四名不同玩家。"
        try:
            await self.rank_db.resolve_users([user_id for user_id, _ in mentions])
        except Exception as exc:
            if self.use_shinjuku_user_database:
                return f"无法创建牌桌：{exc}。请先让四名玩家使用新宿插件完成注册。"
            return f"无法创建牌桌：{exc}"
        initial = max(0, int(self.config.get("initial_points", 25000)))
        table = Table(
            session_id=self._session_id(event),
            players=[
                Player(user_id=user_id, name=name, seat=seat, points=initial)
                for (user_id, name), seat in zip(mentions, SEATS)
            ],
        )
        self.store.save(table)
        return "牌桌已创建。\n" + self._table_text(table)

    async def _score_hand(self, event: AstrMessageEvent, table: Table) -> str:
        mentions = self._mentions(event)
        text = event.get_message_str()
        is_tsumo = "自摸" in text
        is_ron = "荣和" in text or "榮和" in text
        if is_tsumo == is_ron:
            return "请明确使用“@和牌者 自摸”或“@和牌者 荣和 @放铳者”。"
        needed = 1 if is_tsumo else 2
        if len(mentions) < needed:
            return "没有找到完整的和牌者/放铳者艾特信息。"
        winner_id = mentions[0][0]
        loser_id = None if is_tsumo else mentions[1][0]
        player_ids = [player.user_id for player in table.players]
        if winner_id not in player_ids or (loser_id and loser_id not in player_ids):
            return "和牌者和放铳者必须属于当前牌桌。"
        if loser_id == winner_id:
            return "和牌者和放铳者不能是同一个人。"
        images = self._image_urls(event)
        if not images:
            return "请在同一条消息中附上清晰的整桌和牌照片。"

        self._parse_round(text, table)
        result_name = "自摸" if is_tsumo else "荣和"
        try:
            hand = await self._recognize(event, images, result_name)
        except Exception as exc:
            logger.error(f"日麻图片识别失败: {exc}")
            return f"图片识别失败：{exc}"
        self._apply_text_conditions(hand, text)

        confidence = float(hand.get("confidence", 0) or 0)
        threshold = float(self.config.get("vision_confidence_threshold", 0.85))
        uncertain = hand.get("uncertain", [])
        if confidence < threshold or uncertain:
            details = "、".join(str(item) for item in uncertain) or "牌面不够清晰"
            return (
                f"本次识别置信度 {confidence:.0%}，未进入记账：{details}\n"
                "请重新拍摄正上方、无反光且完整包含和牌区域和宝牌区的照片。"
            )

        try:
            score = calculate_score(
                hand,
                winner_id=winner_id,
                loser_id=loser_id,
                player_ids=player_ids,
                dealer_id=self._current_dealer_id(table),
                winner_seat=self._current_seat(table, winner_id),
                round_wind=table.round_wind,
                honba=table.honba,
                riichi_sticks=table.riichi_sticks,
            )
        except Exception as exc:
            return f"牌型已识别，但未通过计分校验：{exc}"

        table.pending = {
            "winner_id": winner_id,
            "loser_id": loser_id,
            "result_type": result_name,
            "hand": hand,
            "han": score.han,
            "fu": score.fu,
            "yaku": score.yaku,
            "level": score.level,
            "payments": score.payments,
            "created_by": str(event.get_sender_id()),
        }
        self.store.save(table)
        names = {player.user_id: player.name for player in table.players}
        lines = [
            f"识别到：{names[winner_id]} {result_name}"
            + (f" {names[loser_id]}" if loser_id else ""),
            f"手牌：{hand.get('closed_tiles')}　和牌张：{hand.get('win_tile')}",
            "役种：" + ("、".join(score.yaku) or "无"),
            f"{score.fu}符 {score.han}番" + (f"（{score.level}）" if score.level else ""),
            "点数变化：",
        ]
        for player in table.players:
            delta = score.payments[player.user_id]
            lines.append(f"{player.name} {delta:+d}")
        lines.extend(["", "发送“麻确认”写入记录，发送“麻取消”放弃。"])
        return "\n".join(lines)

    def _confirm(self, event: AstrMessageEvent, table: Table) -> str:
        pending = table.pending
        if not pending:
            return "当前没有待确认记录。"
        sender = str(event.get_sender_id())
        player_ids = {player.user_id for player in table.players}
        if sender not in player_ids and sender != str(pending.get("created_by")):
            return "只有当前牌桌玩家或本次申报者可以确认。"
        before = {player.user_id: player.points for player in table.players}
        for player in table.players:
            player.points += int(pending["payments"].get(player.user_id, 0))
        table.history.append({"before": before, "record": pending})
        table.pending = None
        table.riichi_sticks = 0
        self.store.save(table)
        return "记账完成。\n" + self._table_text(table)

    def _undo(self, event: AstrMessageEvent, table: Table) -> str:
        if str(event.get_sender_id()) not in {p.user_id for p in table.players}:
            return "只有当前牌桌玩家可以撤销记录。"
        if not table.history:
            return "当前没有可撤销的记录。"
        record = table.history.pop()
        before = record.get("before", {})
        for player in table.players:
            if player.user_id in before:
                player.points = int(before[player.user_id])
        table.pending = None
        self.store.save(table)
        return "已撤销上一局。\n" + self._table_text(table)

    async def _finish_table(self, table: Table) -> str:
        results = await self.rank_db.record_match(
            table.session_id,
            [
                {
                    "platform_id": player.user_id,
                    "name": player.name,
                    "points": player.points,
                }
                for player in table.players
            ],
        )
        lines = ["牌桌已结束，段位结算完成："]
        for item in results:
            lines.append(
                f"{item['placement']}位 {item['name']}：{item['points']}点｜"
                f"{item['rank']} {item['rank_points']}（{item['rank_delta']:+d}）｜"
                f"Rate {item['rating']:.2f}（{item['rating_delta']:+.2f}）"
            )
        self.store.delete(table.session_id)
        return "\n".join(lines)

    async def _dispatch(self, event: AstrMessageEvent) -> str | None:
        text = event.get_message_str().strip()
        if not self._has_trigger(event):
            return None
        async with self._lock:
            session_id = self._session_id(event)
            if "麻帮助" in text or "麻 帮助" in text:
                return self._help_text()
            if "开桌" in text:
                return await self._create_table(event)
            table = self.store.get(session_id)
            if not table:
                return "当前没有牌桌。请先发送：@机器人 麻 开桌 @玩家1 @玩家2 @玩家3 @玩家4"
            if "麻确认" in text or "麻 确认" in text:
                return self._confirm(event, table)
            if "麻取消" in text or "麻 取消" in text:
                table.pending = None
                self.store.save(table)
                return "已取消待确认记录。"
            if "麻撤销" in text or "麻 撤销" in text:
                return self._undo(event, table)
            if "麻结束" in text or "麻 结束" in text:
                try:
                    return await self._finish_table(table)
                except Exception as exc:
                    logger.error(f"日麻段位结算失败: {exc}")
                    return f"段位结算失败，牌桌已保留：{exc}"
            if "麻查询" in text or "麻 查询" in text or "麻状态" in text:
                return self._table_text(table)
            if "自摸" in text or "荣和" in text or "榮和" in text:
                return await self._score_hand(event, table)
            return self._help_text()

    def _help_text(self) -> str:
        return (
            "新宿日麻用法：\n"
            "1. @机器人 麻 开桌 @东家 @南家 @西家 @北家\n"
            "2. @机器人 麻 @和牌者 自摸 东1局 0本场 [整桌照片]\n"
            "3. @机器人 麻 @和牌者 荣和 @放铳者 东1局 0本场 [整桌照片]\n"
            "4. 麻确认 / 麻取消 / 麻查询 / 麻撤销 / 麻结束\n"
            "立直、一发、岭上、抢杠、海底、河底等照片无法证明的条件请写在申报文字中。"
        )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.regex(r"麻")
    async def mahjong_message(self, event: AstrMessageEvent):
        """处理群聊中的新宿日麻请求。"""
        response = await self._dispatch(event)
        if response:
            event.stop_event()
            yield event.plain_result(response)

    @filter.llm_tool(name="shinjuku_mahjong")
    async def shinjuku_mahjong_tool(self, event: AstrMessageEvent, action: str):
        """处理新宿日麻牌桌的创建、整桌照片算分、确认、查询、撤销和结束操作。

        Args:
            action(string): 用户要求执行的日麻操作；原始消息中的艾特、文字和图片由工具自动读取
        """
        response = await self._dispatch(event)
        yield event.plain_result(response or f"无法识别日麻操作：{action}")
