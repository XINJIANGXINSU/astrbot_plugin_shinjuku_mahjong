import asyncio
import sqlite3

import pytest

from rank_db import RankDatabase


def prepare_shinjuku_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        '''
        CREATE TABLE "User" (id INTEGER PRIMARY KEY, "createdAt" TEXT NOT NULL);
        CREATE TABLE "Bind" (id INTEGER PRIMARY KEY, type TEXT NOT NULL, bid TEXT NOT NULL, "userId" INTEGER NOT NULL);
        '''
    )
    for index in range(1, 5):
        conn.execute('INSERT INTO "User" VALUES (?,?)', (index, "2026-08-20"))
        conn.execute('INSERT INTO "Bind" VALUES (?,?,?,?)', (index, "QQ", str(index), index))
    conn.commit()
    conn.close()


def test_linked_rank_match(tmp_path):
    path = tmp_path / "shinjuku.db"
    prepare_shinjuku_db(path)
    database = RankDatabase(path, linked=True)
    result = asyncio.run(
        database.record_match(
            "group:1",
            [
                {"platform_id": "1", "name": "一号", "points": 35000},
                {"platform_id": "2", "name": "二号", "points": 28000},
                {"platform_id": "3", "name": "三号", "points": 22000},
                {"platform_id": "4", "name": "四号", "points": 15000},
            ],
        )
    )
    assert [item["placement"] for item in result] == [1, 2, 3, 4]
    assert result[0]["rank_points"] == 150
    assert result[0]["rating"] == 1516
    conn = sqlite3.connect(path)
    row = conn.execute(
        'SELECT "userId","games","firstCount" FROM "MahjongRank" WHERE "platformId"="1"'
    ).fetchone()
    conn.close()
    assert row == (1, 1, 1)


def test_linked_rank_rejects_unregistered_player(tmp_path):
    path = tmp_path / "shinjuku.db"
    prepare_shinjuku_db(path)
    database = RankDatabase(path, linked=True)
    with pytest.raises(ValueError, match="尚未注册"):
        asyncio.run(database.resolve_users(["999"]))
