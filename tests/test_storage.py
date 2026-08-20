from storage import Player, Table, TableStore


def test_table_round_trip(tmp_path):
    store = TableStore(tmp_path / "tables.json")
    table = Table(
        session_id="group:1",
        players=[Player("1", "玩家1", "east", 25000)],
    )
    store.save(table)
    loaded = store.get("group:1")
    assert loaded is not None
    assert loaded.players[0].name == "玩家1"
    assert loaded.players[0].points == 25000

