#!/usr/bin/env python3
"""組み立て: 正規化済み中間データ → 最終 Anima プロンプト(Crody's Anima Guide 準拠)。

並び(Crody's Anima Guide のお手本構造):
  品質/meta/rating,
  人数, 単純な相互干渉({CHAR_n}を含まない関係タグ),
  全キャラの識別子をまとめて列挙(Character / General Concept),
  各キャラのブロック { 識別子, 容姿, 表情, ポーズ(インライン) } を順に,
  カメラ, 背景, ライティング,
  自然文の補助(任意): 位置 と 特定個人への方向性のある相互干渉。
コメントは出力しない。改行も入れない(Crody)。

識別子(オリジナルのキャラ): "<服タグ> <female|male|other>"。
  性別語は人数から: 1girl->female, 1boy->male, 1other->other(子供を若く描く 1girl/1boy の
  名詞を避け、成人として表す)。`mature female`/`mature male` は female/male と冗長なので落とす。
  名前付きキャラは BASE の名前をそのまま識別子に使う。

使い方:
  python3 assemble.py --in normalized.txt --out final_prompt.txt
  cat normalized.txt | python3 assemble.py --rating safe
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

QUALITY_HEAD = "masterpiece, best quality, score_9, score_8, score_7"
QUALITY_TAIL = "year 2025, newest, highres, absurdres, very aesthetic, scenery"
DEFAULT_NEGATIVE = ("worst quality, low quality, early, old, score_1, score_2, score_3, "
                    "cartoon, graphic, painting, crayon, graphite, abstract, glitch, "
                    "deformed, mutated, ugly, disfigured, long body, bad anatomy, "
                    "bad hands, missing fingers, extra fingers, extra digits, fewer digits, "
                    "cropped, very displeasing, artist name, blurry, jpeg artifacts, "
                    "lowres, censor")
VALID_RATINGS = {"safe", "sensitive", "nsfw", "explicit"}

# 各キャラブロックの並び。ポーズはインライン(同居)。容姿のうち下記は female/male と冗長で落とす。
MATURITY_REDUNDANT = {"mature female", "mature male"}
GENDER_FROM_NOUN = {"girl": "female", "boy": "male", "other": "other"}
SCENE_ORDER = ["CAMERA", "BACKGROUND", "LIGHTING_AND_EFFECTS"]

_COUNT_TOKEN_RE = re.compile(
    r"^(?:\d+(?:girls?|boys?|others?)|multiple (?:girls|boys|others)|solo|duo|trio|group)$")
_COUNT_PREFIX_RE = re.compile(r"^\s*\d+\s*")
_CHAR_REF_RE = re.compile(r"\{CHAR_(\d+)\}", re.IGNORECASE)


def parse_blocks(text: str) -> dict:
    blocks: dict[str, dict] = {}
    char_order: list[str] = []
    current = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            current = s[1:-1]
            blocks.setdefault(current, {})
            if current.startswith("CHARACTER_"):
                char_order.append(current)
            continue
        m = re.match(r"^-\s*([A-Z_0-9]+)\s*:\s*(.*)$", s)
        if m and current:
            blocks[current][m.group(1)] = m.group(2).strip()
    return {"blocks": blocks, "char_order": char_order}


def tags_of(value: str) -> list[str]:
    if not value or value.strip().lower() == "none":
        return []
    return [t.strip() for t in value.split(",") if t.strip()]


def person_noun(base: str) -> str:
    base_tags = tags_of(base)
    if not base_tags:
        return "person"
    noun = _COUNT_PREFIX_RE.sub("", base_tags[0]).strip()
    return noun or "person"


def gender_word(base: str) -> str:
    noun = person_noun(base)
    return GENDER_FROM_NOUN.get(noun, noun)  # 名前付きキャラはその名前


def char_identity(cb: dict) -> str:
    """識別子 = "<服タグ> <gender>"。服が無ければ容姿(冗長な成熟タグを除く)、それも無ければ gender。"""
    g = gender_word(cb.get("BASE", ""))
    desc = tags_of(cb.get("OUTFIT", ""))
    if not desc:
        desc = [t for t in tags_of(cb.get("APPEARANCE", "")) if t not in MATURITY_REDUNDANT]
    return (" ".join(desc) + " " + g) if desc else g


def dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in seq:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def char_block(cb: dict) -> list[str]:
    """1キャラ: 識別子, 容姿(冗長な成熟タグ除く), 表情, ポーズ(インライン)。
    服は識別子に取り込み済みなので特徴には足さない。"""
    block = [char_identity(cb)]
    for t in tags_of(cb.get("APPEARANCE", "")):
        if t not in MATURITY_REDUNDANT:
            block.append(t)
    block.extend(tags_of(cb.get("EXPRESSION", "")))
    block.extend(tags_of(cb.get("POSE", "")))
    return dedup(block)


def capitalize_sentences(text: str) -> str:
    text = re.sub(r"^(\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    text = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text


def build(text: str, rating_override: str | None = None) -> str:
    parsed = parse_blocks(text)
    blocks, char_order = parsed["blocks"], parsed["char_order"]

    # 品質 + rating(score直後) + era/meta
    head = tags_of(QUALITY_HEAD)
    rating = "safe"
    r = (blocks.get("METRICS", {}).get("RATING", "") or "").strip().lower()
    if r in VALID_RATINGS:
        rating = r
    if rating_override and rating_override.lower() in VALID_RATINGS:
        rating = rating_override.lower()
    head.append(rating)
    head.extend(tags_of(QUALITY_TAIL))

    # 人数 + 単純な相互干渉({CHAR_n}を含まない関係タグ)
    for t in tags_of(blocks.get("INTERACTION", {}).get("COUNT_AND_RELATION", "")):
        if _COUNT_TOKEN_RE.match(t.lower()):
            head.append(t)
        elif "{" not in t:
            head.append(t)
    head = dedup(head)

    # 識別子(各キャラ)
    identities = {cid: char_identity(blocks.get(cid, {})) for cid in char_order}

    # セグメントを順に連結(コメントなし。キャラ間/概念行では重複除去しない=Crodyは再掲する)
    seg_lists: list[list[str]] = [head]
    if len(char_order) >= 2:  # Character / General Concept: 全キャラの識別子をまとめて列挙
        seg_lists.append([identities[cid] for cid in char_order])
    for cid in char_order:  # 各キャラのブロック(ポーズ同居)
        seg_lists.append(char_block(blocks.get(cid, {})))

    scene = blocks.get("SCENE_DETAILS", {})
    scene_tags: list[str] = []
    for f in SCENE_ORDER:
        scene_tags.extend(tags_of(scene.get(f, "")))
    scene_tags = dedup(scene_tags)
    if scene_tags:
        seg_lists.append(scene_tags)

    tag_line = ", ".join(", ".join(seg) for seg in seg_lists if seg)

    # 自然文の補助: 位置 + 特定個人への方向性のある相互干渉({CHAR_n}を識別子へ)
    caption = (blocks.get("NATURAL_LANGUAGE", {}).get("CAPTION", "") or "").strip()
    if caption and char_order:
        def repl(m):
            return identities.get(f"CHARACTER_{m.group(1)}", m.group(0))
        caption = capitalize_sentences(_CHAR_REF_RE.sub(repl, caption))

    if caption:
        positive_line = f"{tag_line}. {caption}"
        if not positive_line.rstrip().endswith((".", "!", "?")):
            positive_line += "."
    else:
        positive_line = tag_line + ","

    return f"POSITIVE: {positive_line}\nNEGATIVE: {DEFAULT_NEGATIVE},"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in", dest="infile", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--rating", default=None,
                   help="レーティング上書き {safe|sensitive|nsfw|explicit}(既定 safe)")
    args = p.parse_args(argv)
    if args.rating is not None and args.rating.lower() not in VALID_RATINGS:
        print(f"エラー: --rating は {sorted(VALID_RATINGS)} のいずれか。", file=sys.stderr)
        return 1
    text = args.infile.read_text(encoding="utf-8") if args.infile else sys.stdin.read()
    result = build(text, rating_override=args.rating)
    if args.out:
        args.out.write_text(result + "\n", encoding="utf-8")
        print(f"出力: {args.out}", file=sys.stderr)
    else:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
