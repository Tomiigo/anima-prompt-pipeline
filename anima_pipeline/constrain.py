"""出力の整形と、機械的ルールの検証。

ここでは語彙(使ってよいタグ)の強制はしない。タグの並べ替えもしない —— Gemma が
anima_rules.txt の順序どおりに並べた結果をそのまま尊重する。役割は 2 つ:
  * render(obj)    - {tags, natural} を最終的な Anima プロンプト文字列へ整形する。
  * validate(text) - カンマ+スペースなどの機械的な体裁だけを点検する。
"""
from __future__ import annotations
import re


# --------------------------------------------------------------------------
# 構造化出力 -> 最終的な Anima プロンプト文字列
# --------------------------------------------------------------------------
def render(obj: dict) -> str:
    """{"tags": [...], "natural": "..." または [...]} を Anima プロンプト文字列へ整形する。
    タグは渡された順序のまま(並べ替えない)。カンマの後は単一スペース。自然文があれば
    末尾にピリオド付きで足す。"""
    tags = [str(t).strip() for t in obj.get("tags", []) if str(t).strip()]
    tag_part = ", ".join(tags)

    nat = obj.get("natural", "")
    if isinstance(nat, list):
        sents_in = [str(s).strip() for s in nat if str(s).strip()]
    else:
        sents_in = [str(nat).strip()] if str(nat).strip() else []
    sents: list[str] = []
    for s in sents_in:
        s = re.sub(r"\s+", " ", s).strip().rstrip(".")
        if s:
            sents.append(s + ".")
    nat_part = " ".join(sents)

    if tag_part and nat_part:
        return f"{tag_part}. {nat_part}"
    return tag_part or nat_part


# --------------------------------------------------------------------------
# 機械的ルールの検証
# --------------------------------------------------------------------------
# Anima はマークダウン記号を文字どおり描いてしまうため、それだけは点検する。
_MARKDOWN = re.compile(r"[#*`>]|\]\([^)]*\)")


def validate(prompt: str) -> list[str]:
    """機械的な体裁だけを点検する。語彙・代名詞・重みは点検しない。
    代名詞は Anima ガイドが自然文での使用を認めているため許可する。重みは 1.2〜1.4 を
    使う仕様のため、低い重みも問題にしない。"""
    issues: list[str] = []
    if not prompt.strip():
        return ["empty output"]
    if "  " in prompt:
        issues.append("double space")
    if re.search(r",(?=\S)", prompt):
        issues.append("missing space after comma")
    if ",," in prompt or ", ," in prompt:
        issues.append("empty/double comma")
    if _MARKDOWN.search(prompt):
        issues.append("markdown-like characters (Anima draws them literally)")
    return issues
