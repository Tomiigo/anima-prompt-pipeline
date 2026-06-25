#!/usr/bin/env python3
"""組み立て: 正規化済み中間データ → 最終 Anima プロンプト(Crody's Anima Guide 準拠)。

並び(Crody's Anima Guide の Segmented Prompt 構造に準拠):
  品質/meta/rating,
  人数,
  概念行(Character / General Concept): 自然文で各キャラと髪を with/and で束ねる
    例 "female with blonde hair and female with black hair",
  アクション(Pose / Action): シーン全体の動作。概念行と最初のキャラの仕切りを兼ねる,
  各キャラのブロック { アンカー, 容姿(髪/目), 表情, ポーズ, 服 } を順に(服は末尾),
  ボディ・リレーション(Interaction / Pose / Body Relationship): 方向性のある相互干渉。
    CAPTION の {CHAR_n} を短いアンカーへ置換し、キャラの後・シーンの前に置く,
  カメラ, 背景, ライティング。
出力は1行・小文字基調・末尾の句点なし(Crody)。コメントは出力しない。

アンカー(キャラを指す短い識別子): "<1特徴(基本は髪)> <female|male|other>"。
  概念行・ボディリレーションで共通して使う。フル特徴は各キャラブロックに集約する。
  性別語は人数から: 1girl->female, 1boy->male, 1other->other。
  `mature female`/`mature male` は female/male と冗長なので落とす。
  名前付きキャラは BASE の名前をそのまま使う。

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
_HAIR_RE = re.compile(r"\bhair\b")


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


def char_anchor(cb: dict) -> str:
    """キャプション(自然文)でキャラを指す短い識別子。特徴を網羅すると Anima を混乱
    させ、属性がにじむため、最も識別力の高い1要素(基本は髪)+性別だけにする。
    名前付きキャラは名前をそのまま使う。識別できる appearance が無ければ性別のみ。"""
    noun = person_noun(cb.get("BASE", ""))
    g = gender_word(cb.get("BASE", ""))
    if noun not in GENDER_FROM_NOUN:  # 名前付きキャラ
        return g
    appearance = [t for t in tags_of(cb.get("APPEARANCE", "")) if t not in MATURITY_REDUNDANT]
    hair = next((t for t in appearance if _HAIR_RE.search(t)), None)
    if hair:
        return f"{hair} {g}"
    if appearance:
        return f"{appearance[0]} {g}"
    return g


def dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in seq:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def concept_phrase(cb: dict) -> str:
    """概念行(Character / General Concept)用の自然文の断片。Crody の
    "<identity> with <hair>" に倣う。識別子=性別(名前付きは名前)、髪があれば with で結ぶ。"""
    noun = person_noun(cb.get("BASE", ""))
    g = gender_word(cb.get("BASE", ""))
    if noun not in GENDER_FROM_NOUN:  # 名前付きキャラ
        return g
    appearance = [t for t in tags_of(cb.get("APPEARANCE", "")) if t not in MATURITY_REDUNDANT]
    hair = next((t for t in appearance if _HAIR_RE.search(t)), None)
    return f"{g} with {hair}" if hair else g


def char_block(cb: dict) -> list[str]:
    """1キャラ(Crody順): アンカー → 容姿(髪/目) → 表情 → ポーズ → 服。
    服はキャラ内の最後に置く(Crody の identity→hair→expression→outfit に対応)。
    アンカーは短く保ち、フル特徴はこのブロックに集約する。"""
    block = [char_anchor(cb)]
    block.extend(t for t in tags_of(cb.get("APPEARANCE", "")) if t not in MATURITY_REDUNDANT)
    block.extend(tags_of(cb.get("EXPRESSION", "")))
    block.extend(tags_of(cb.get("POSE", "")))
    block.extend(tags_of(cb.get("OUTFIT", "")))
    return dedup(block)


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

    # 人数は head に。相互干渉(アクション)は分離して、概念行とキャラの間に挟む。
    count_tags: list[str] = []
    action_tags: list[str] = []
    for t in tags_of(blocks.get("INTERACTION", {}).get("COUNT_AND_RELATION", "")):
        if _COUNT_TOKEN_RE.match(t.lower()):
            count_tags.append(t)
        elif "{" not in t:
            action_tags.append(t)
    head.extend(count_tags)
    head = dedup(head)
    action_tags = dedup(action_tags)

    # 各キャラの短いアンカー(1特徴+性別)。ボディ・リレーション(自然文)で使う。
    anchors = {cid: char_anchor(blocks.get(cid, {})) for cid in char_order}

    # ボディ・リレーション(方向性のある相互干渉)を先に組み立てる。
    # CAPTION の {CHAR_n} を短いアンカーへ置換。Crody は1行・小文字なので大文字化しない。
    caption = (blocks.get("NATURAL_LANGUAGE", {}).get("CAPTION", "") or "").strip()
    relation = ""
    if caption and caption.lower() != "none" and char_order:
        def repl(m):
            return anchors.get(f"CHARACTER_{m.group(1)}", m.group(0))
        relation = _CHAR_REF_RE.sub(repl, caption).rstrip(" .!?")

    scene = blocks.get("SCENE_DETAILS", {})
    scene_tags: list[str] = []
    for f in SCENE_ORDER:
        scene_tags.extend(tags_of(scene.get(f, "")))
    scene_tags = dedup(scene_tags)

    # セグメントを Crody の Segmented 構造順に連結(1行・コメントなし)。
    # head(品質/meta/rating/人数) → 概念行(自然文 "A with hair and B with hair")
    #   → アクション(仕切り) → 各キャラブロック(服は末尾) → ボディ・リレーション → シーン。
    seg_lists: list[list[str]] = [head]
    if len(char_order) >= 2:  # Character / General Concept: 自然文で各キャラと髪を with で束ねる
        concept = " and ".join(concept_phrase(blocks.get(cid, {})) for cid in char_order)
        seg_lists.append([concept])
    if action_tags:
        seg_lists.append(action_tags)
    for cid in char_order:  # 各キャラのブロック(服を末尾に)
        seg_lists.append(char_block(blocks.get(cid, {})))
    if relation:  # Interaction / Pose / Body Relationship: キャラの後・シーンの前
        seg_lists.append([relation])
    if scene_tags:
        seg_lists.append(scene_tags)

    positive_line = ", ".join(", ".join(seg) for seg in seg_lists if seg)

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
