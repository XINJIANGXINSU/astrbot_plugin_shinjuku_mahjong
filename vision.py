from __future__ import annotations

import json
import re
from typing import Any


VISION_SYSTEM_PROMPT = """你是日本立直麻将牌谱识别器。你的任务仅是从整桌照片读取和牌者已经摊开的牌、鸣牌、和牌张和宝牌指示牌，不负责计算番数或点数。

只输出一个 JSON 对象，不要输出 Markdown。牌使用 mpsz 表示法：万=m、筒=p、索=s、字牌=z（1东2南3西4北5白6发7中），赤五使用 0m/0p/0s。

JSON 字段：
{
  "closed_tiles": "包含和牌张的门前部分，例如123m456p789s55z",
  "win_tile": "单独一张，例如6p",
  "melds": [{"type":"chi|pon|kan|shouminkan","tiles":"123m","opened":true}],
  "dora_indicators": ["4p"],
  "ura_dora_indicators": [],
  "riichi": false,
  "double_riichi": false,
  "ippatsu": false,
  "rinshan": false,
  "chankan": false,
  "haitei": false,
  "houtei": false,
  "tenhou": false,
  "chiihou": false,
  "confidence": 0.0,
  "uncertain": ["无法确认的内容"]
}

整桌中只选择与申报结果对应、已经完整摊开的和牌区域。看不清时降低 confidence 并写入 uncertain，严禁猜牌。立直、一发、岭上、抢杠、海底等照片通常无法证明，除非用户文字明确提供，否则保持 false。"""


def build_prompt(user_text: str, result_type: str) -> str:
    return (
        f"用户申报：{result_type}\n"
        f"用户补充文字：{user_text}\n"
        "请识别整桌照片中的和牌牌型。closed_tiles 必须包含和牌张；副露牌不要重复写入 closed_tiles。"
    )


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError("视觉模型没有返回 JSON") from None
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("视觉模型返回格式不是对象")
    return value

