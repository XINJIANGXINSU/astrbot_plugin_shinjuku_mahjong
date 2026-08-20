from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Player:
    user_id: str
    name: str
    seat: str
    points: int


@dataclass
class Table:
    session_id: str
    players: list[Player]
    round_wind: str = "east"
    round_number: int = 1
    honba: int = 0
    riichi_sticks: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, Any] | None = None


class TableStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(self.path)

    def get(self, session_id: str) -> Table | None:
        raw = self._read().get(session_id)
        if not isinstance(raw, dict):
            return None
        players = [Player(**item) for item in raw.pop("players", [])]
        return Table(players=players, **raw)

    def save(self, table: Table) -> None:
        data = self._read()
        data[table.session_id] = asdict(table)
        self._write(data)

    def delete(self, session_id: str) -> bool:
        data = self._read()
        if session_id not in data:
            return False
        del data[session_id]
        self._write(data)
        return True

