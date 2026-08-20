from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite


RANKS = (
    (19000, "魂天"),
    (15500, "雀圣 III"),
    (12500, "雀圣 II"),
    (10000, "雀圣 I"),
    (8200, "雀豪 III"),
    (6600, "雀豪 II"),
    (5200, "雀豪 I"),
    (4000, "雀杰 III"),
    (3000, "雀杰 II"),
    (2200, "雀杰 I"),
    (1500, "雀士 III"),
    (1000, "雀士 II"),
    (500, "雀士 I"),
    (0, "初心者"),
)

PLACEMENT_BASE = {1: 100, 2: 40, 3: -30, 4: -80}


def rank_name(points: int) -> str:
    return next(name for threshold, name in RANKS if points >= threshold)


def rank_delta(placement: int, final_points: int, baseline: int = 25000) -> int:
    point_bonus = round((final_points - baseline) / 1000) * 5
    return PLACEMENT_BASE[placement] + max(-100, min(100, point_bonus))


class RankDatabase:
    def __init__(self, path: Path, linked: bool):
        self.path = path
        self.linked = linked
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS "MahjongRank" (
                "platformId" TEXT PRIMARY KEY,
                "userId" INTEGER,
                "rating" REAL NOT NULL DEFAULT 1500,
                "rankPoints" INTEGER NOT NULL DEFAULT 0,
                "games" INTEGER NOT NULL DEFAULT 0,
                "firstCount" INTEGER NOT NULL DEFAULT 0,
                "secondCount" INTEGER NOT NULL DEFAULT 0,
                "thirdCount" INTEGER NOT NULL DEFAULT 0,
                "fourthCount" INTEGER NOT NULL DEFAULT 0,
                "totalFinalPoints" INTEGER NOT NULL DEFAULT 0,
                "highestFinalPoints" INTEGER,
                "bustCount" INTEGER NOT NULL DEFAULT 0,
                "updatedAt" TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mahjong_rank_user ON "MahjongRank"("userId");
            CREATE TABLE IF NOT EXISTS "MahjongMatch" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                "sessionKey" TEXT NOT NULL,
                "endedAt" TEXT NOT NULL,
                result TEXT NOT NULL
            );
            """
        )
        await conn.commit()
        return conn

    async def resolve_users(self, platform_ids: list[str]) -> dict[str, int | None]:
        conn = await self._connect()
        try:
            resolved: dict[str, int | None] = {}
            for platform_id in platform_ids:
                user_id = None
                if self.linked:
                    cursor = await conn.execute(
                        'SELECT "userId" FROM "Bind" WHERE type=? AND bid=? LIMIT 1',
                        ("QQ", platform_id),
                    )
                    row = await cursor.fetchone()
                    await cursor.close()
                    if row is None:
                        raise ValueError(f"QQ {platform_id} 尚未注册新宿用户")
                    user_id = int(row["userId"])
                resolved[platform_id] = user_id
                await conn.execute(
                    'INSERT INTO "MahjongRank" ("platformId","userId","updatedAt") VALUES (?,?,?) '
                    'ON CONFLICT("platformId") DO UPDATE SET "userId"=excluded."userId"',
                    (platform_id, user_id, datetime.now().isoformat()),
                )
            await conn.commit()
            return resolved
        finally:
            await conn.close()

    async def record_match(self, session_key: str, players: list[dict[str, Any]]) -> list[dict[str, Any]]:
        platform_ids = [str(item["platform_id"]) for item in players]
        user_ids = await self.resolve_users(platform_ids)
        ordered = sorted(players, key=lambda item: (-int(item["points"]), platform_ids.index(str(item["platform_id"]))))
        placements = {str(item["platform_id"]): index + 1 for index, item in enumerate(ordered)}
        baseline = round(sum(int(item["points"]) for item in players) / len(players))
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            rows: dict[str, aiosqlite.Row] = {}
            for platform_id in platform_ids:
                cursor = await conn.execute(
                    'SELECT * FROM "MahjongRank" WHERE "platformId"=?', (platform_id,)
                )
                rows[platform_id] = await cursor.fetchone()
                await cursor.close()

            updates = []
            for item in players:
                platform_id = str(item["platform_id"])
                points = int(item["points"])
                placement = placements[platform_id]
                row = rows[platform_id]
                old_rating = float(row["rating"])
                pair_score = 0.0
                pair_expected = 0.0
                for opponent_id in platform_ids:
                    if opponent_id == platform_id:
                        continue
                    opponent_rating = float(rows[opponent_id]["rating"])
                    pair_expected += 1 / (1 + 10 ** ((opponent_rating - old_rating) / 400))
                    opponent_place = placements[opponent_id]
                    pair_score += 1.0 if placement < opponent_place else 0.0
                rating_change = round(32 * (pair_score - pair_expected) / 3, 2)
                new_rating = round(old_rating + rating_change, 2)
                delta = rank_delta(placement, points, baseline)
                new_rank_points = max(0, int(row["rankPoints"]) + delta)
                counts = [int(row[name]) for name in ("firstCount", "secondCount", "thirdCount", "fourthCount")]
                counts[placement - 1] += 1
                highest = max(points, int(row["highestFinalPoints"] or points))
                await conn.execute(
                    'UPDATE "MahjongRank" SET "userId"=?,"rating"=?,"rankPoints"=?,"games"="games"+1,'
                    '"firstCount"=?,"secondCount"=?,"thirdCount"=?,"fourthCount"=?,'
                    '"totalFinalPoints"="totalFinalPoints"+?,"highestFinalPoints"=?,"bustCount"="bustCount"+?,"updatedAt"=? '
                    'WHERE "platformId"=?',
                    (
                        user_ids[platform_id], new_rating, new_rank_points, *counts, points, highest,
                        1 if points < 0 else 0, datetime.now().isoformat(), platform_id,
                    ),
                )
                updates.append(
                    {
                        "platform_id": platform_id,
                        "name": item["name"],
                        "placement": placement,
                        "points": points,
                        "rank": rank_name(new_rank_points),
                        "rank_points": new_rank_points,
                        "rank_delta": delta,
                        "rating": new_rating,
                        "rating_delta": rating_change,
                    }
                )
            await conn.execute(
                'INSERT INTO "MahjongMatch" ("sessionKey","endedAt",result) VALUES (?,?,?)',
                (session_key, datetime.now().isoformat(), json.dumps(updates, ensure_ascii=False)),
            )
            await conn.commit()
            return sorted(updates, key=lambda item: item["placement"])
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()
