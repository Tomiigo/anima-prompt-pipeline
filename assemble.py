#!/usr/bin/env python3
"""組み立て: 正規化済み中間データ → 最終 Anima プロンプト。

並び(Anima 公式モデルカードのタグ順 + Crody の Segmented Prompt):
  品質/meta/年/rating,
  人数,
  版権キャラ, 作品                     ← 公式カードの [character] [series]
  概念行(2人以上): 各キャラと髪を with/and で束ねる
    例 "female with blonde hair and female with black hair",
  アクション(Pose / Action): シーン全体の動作。概念行と最初のキャラの仕切りを兼ねる,
  各キャラのブロック { 識別子, 身体, 髪, 目, 表情, 装身具, 服, ポーズ },
  シーン(カメラ → 光 → 背景),
  自然文(位置・方向のある相互干渉)。最後に置く。
出力は1行・小文字基調・末尾の句点なし(Crody)。コメントは出力しない。
タグの括弧は \\( \\) にする(ComfyUI は括弧を重み付けの記法として読むため)。
アーティストのタグは扱わない。

アンカー(自然文で登場人物を指す識別子):
  名前を持たない登場人物は
    女性     髪+髪の色 / 肌の色 / 体型 / 性別
    男性     肌の色 / 体型 / 性別
    ロボット 表面の色 / 体型 / robot
  で構成する。版権キャラは名前ひとつで表し、外見のタグは用いない。
  性別語は人数から: 1girl->female, 1boy->male, 1other->other。
  性別や年齢を含む容姿の語(mature female, young adult male など)は、性別語と重なるので落とす。

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
NONE_VALUES = {"none", "none visible", "not visible", "n/a", "na", "unknown", "unspecified",
               "nothing", "no outfit", "not applicable"}

# 性別や成人を表す容姿の語。キャラの識別子(female / male)と重なるので落とす。
# 旧版は mature female / mature male だけを落としており、young adult male などが残って
# アンカーが "young adult male male" になっていた。
GENDER_REDUNDANT = {
    "female", "male", "other", "woman", "man", "girl", "boy", "adult", "young adult",
    "mature female", "mature male", "adult female", "adult male",
    "young adult female", "young adult male", "young woman", "young man",
}

# 人数タグ(1girl 等)を人物側の語へ落とす。
#   Anima は booru タグで学習されており、成人の女性と男性が1人ずつ描かれた絵でも
#   "1girl, 1boy" とタグ付けされている。人数タグは成人を指す語にならない。
#   登場人物をタグの羅列で構成するときと自然文では、成人女性は female、成人男性は male。
#   人数タグ自体は head に1度だけ置き、人物ごとには繰り返さない。
GENDER_FROM_NOUN = {"girl": "female", "boy": "male", "other": "other"}

# シーンの並び。Crody の記事 §3「Segmented Prompt」の実物に合わせる。背景は光より後。
SCENE_ORDER = ["CAMERA", "LIGHTING_AND_EFFECTS", "BACKGROUND"]

# ---- Crody §3 のキャラ内スロットへ振り分けるための判定 -------------------------
# 「髪」「目」「上衣」のような細分類はどのデータにも無いため、語形の規則で判定する。
_NOT_HAIR_RE = re.compile(
    r"(^|[ _])(hair ornaments?|hairbands?|hair bands?|hairclips?|hair clips?|hair bows?|"
    r"hair ribbons?|hair flowers?|hairpins?|hair pins?|hair bobbles|hair tubes|hair rings|"
    r"hair ties?|hair scrunchies?|hair beads|hair sticks?)$")

# 髪型の語。"hair" を含まないので拾えないが、Crody §3 では [Appearance / Hair] に属する。
_HAIRSTYLE_RE = re.compile(
    r"^(twintails?|ponytail|twin braids|braids?|bangs|blunt bangs|choppy bangs|swept bangs|"
    r"sidelocks|long sidelocks|ahoge|bob cut|hime cut|updo|bun|double bun|side bun|"
    r"drill hair|ringlets|afro|dreadlocks|messy|curly|wavy|straight)$|"
    r"(bangs|sidelocks|braid|braids|ponytail|twintails|bun|buns)$")
_NOT_EYE_RE = re.compile(
    r"(^|[ _])(eyewear|eyepatch|eyeliner|eyeshadow|eyelashes|eyebrow|eyebrows|"
    r"eye mask|eye black|eyewear strap)$|eyebrows$|eyewear$")
# 目に見えるが表情・視線であって容姿の目ではないもの。表情へ送る。
_EYE_IS_EXPRESSION_RE = re.compile(
    r"^(one eye closed|eyes closed|closed eyes|wide-eyed|one-eyed|one eye covered|"
    r"eyes opened|half-closed eyes|narrowed eyes|rolling eyes)$")
_SKIN_RE = re.compile(r"(^|[ _])(skin|skinned female|skinned male)$|^(tan|pale|dark-skinned.*)$")
_BODY_RE = re.compile(
    r"^(slim|slender|thin|petite|curvy|voluptuous|muscular|muscle|toned|plump|chubby|"
    r"fat|athletic|lanky|tall|short|wide hips|narrow waist)\b|"
    r"\b(muscular|slim|curvy|petite|toned|athletic|mature) (torso|body|build|figure)$")

_COUNT_TOKEN_RE = re.compile(
    r"^(?:\d+(?:girls?|boys?|others?)|multiple (?:girls|boys|others)|solo|duo|trio|group)$")
_COUNT_PREFIX_RE = re.compile(r"^\s*\d+\s*")
_CHAR_REF_RE = re.compile(r"\{CHAR_(\d+)\}", re.IGNORECASE)
_UNESCAPED_PAREN_RE = re.compile(r"(?<!\\)([()])")


def _is_hair(t: str) -> bool:
    if _NOT_HAIR_RE.search(t):
        return False
    return "hair" in t or bool(_HAIRSTYLE_RE.search(t))


def _is_eye(t: str) -> bool:
    return ("eye" in t or "pupil" in t) and not _NOT_EYE_RE.search(t) \
        and not _EYE_IS_EXPRESSION_RE.match(t) and not _is_hair(t)


def _is_accessory_from_appearance(t: str) -> bool:
    """APPEARANCE に来たが、Crody §3 では装身具に属する語(髪飾り・アイウェアなど)。"""
    return bool(_NOT_HAIR_RE.search(t) or _NOT_EYE_RE.search(t))


def split_appearance(tags: list[str]) -> dict:
    """APPEARANCE を Crody §3 のスロットへ割る。その他(肌・耳・角・翼など) → 髪 → 目 の順。"""
    out = {"other": [], "hair": [], "eyes": [], "accessory": [], "expression": []}
    for t in tags:
        if _is_accessory_from_appearance(t):
            out["accessory"].append(t)
        elif _is_hair(t):
            out["hair"].append(t)
        elif _EYE_IS_EXPRESSION_RE.match(t):
            out["expression"].append(t)
        elif _is_eye(t):
            out["eyes"].append(t)
        else:
            out["other"].append(t)
    return out


def merge_hair(hair_tags: list[str]) -> str | None:
    """アンカー用に髪を1語へまとめる。"long hair" + "blonde hair" -> "long blonde hair"。
    末尾が hair の語だけをまとめ、twintails 等の型は触らない。"""
    mods: list[str] = []
    for t in hair_tags:
        if t.endswith("hair"):
            m = t[: -len("hair")].strip()
            if m and m not in mods:
                mods.append(m)
    if not mods:
        return hair_tags[0] if hair_tags else None
    return " ".join(mods) + " hair"


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
    if not value or value.strip().lower() in NONE_VALUES:
        return []
    return [t.strip() for t in value.split(",")
            if t.strip() and t.strip().lower() not in NONE_VALUES]


def person_noun(base: str) -> str:
    base_tags = tags_of(base)
    if not base_tags:
        return "person"
    noun = _COUNT_PREFIX_RE.sub("", base_tags[0]).strip()
    return noun or "person"


def gender_word(base: str) -> str:
    noun = person_noun(base)
    return GENDER_FROM_NOUN.get(noun, noun)  # 版権キャラはその名前


def is_named(cb: dict) -> bool:
    return person_noun(cb.get("BASE", "")) not in GENDER_FROM_NOUN


def appearance_tags(cb: dict) -> list[str]:
    return [t for t in tags_of(cb.get("APPEARANCE", "")) if t.lower() not in GENDER_REDUNDANT]


def char_anchor(cb: dict, inline: bool = False) -> str:
    """自然文(概念行・自然文の補足)で登場人物を指す識別子。

    名前を持たない登場人物は、女性は「髪+髪の色 / 肌の色 / 体型 / 性別」、男性は
    「肌の色 / 体型 / 性別」、ロボット等は「表面の色 / 体型 / robot」で構成する。
    版権キャラは名前ひとつで表し、外見のタグは用いない。
    inline=True は文の中に差し込むとき。要素をスペースで繋ぐ(カンマだと文が割れる)。
    """
    g = gender_word(cb.get("BASE", ""))
    if is_named(cb):
        return g

    parts = split_appearance(appearance_tags(cb))
    skin = next((t for t in parts["other"] if _SKIN_RE.search(t)), None)
    body = next((t for t in parts["other"] if _BODY_RE.match(t)), None)

    out: list[str] = []
    if g == "female":                      # 女性のみ髪を含める
        hair = merge_hair(parts["hair"])
        if hair:
            out.append(hair)
    elif g == "other":                     # ロボット等: 表面の色を髪の位置に置く
        surface = next((t for t in parts["other"]
                        if t not in (skin, body) and "color" in t), None)
        if surface:
            out.append(surface)
    if skin:
        out.append(skin)
    if body:
        out.append(body)
    out.append(g)
    return (" " if inline else ", ").join(out)


def dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in seq:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def concept_phrase(cb: dict) -> str:
    """概念行用の自然文の断片。識別子を with で髪に結ぶ。版権キャラは名前のみ。"""
    g = gender_word(cb.get("BASE", ""))
    if is_named(cb):
        return g
    hair = merge_hair(split_appearance(appearance_tags(cb))["hair"])
    return f"{g} with {hair}" if hair else g


def char_block(cb: dict, multi: bool) -> list[str]:
    """1キャラのタグ列(Crody §3 の Segmented Prompt の実物の順)。

      識別子 → 身体の特徴(肌・耳・角・halo・翼) → 髪 → 目 → 表情・視線
      → 装身具 → 服 → ポーズ

    版権キャラの名前は head(人数の直後)に置くので、1人のときは繰り返さない。
    2人以上のときは、どの特徴が誰のものかを結び付けるために名前で始める。
    """
    parts = split_appearance(appearance_tags(cb))
    block: list[str] = []
    if not is_named(cb) or multi:
        block.append(gender_word(cb.get("BASE", "")))
    block.extend(parts["other"])
    block.extend(parts["hair"])
    block.extend(parts["eyes"])
    block.extend(parts["expression"])          # APPEARANCE に紛れた表情語
    block.extend(tags_of(cb.get("EXPRESSION", "")))
    block.extend(parts["accessory"])           # APPEARANCE から動かした装身具
    block.extend(tags_of(cb.get("OUTFIT", "")))
    block.extend(tags_of(cb.get("POSE", "")))
    return dedup(block)


def escape_parens(s: str) -> str:
    """ComfyUI は ( ) を重み付けの記法として読むので、タグの括弧を \\( \\) にする。
    例 fern (sousou no frieren) -> fern \\(sousou no frieren\\)"""
    return _UNESCAPED_PAREN_RE.sub(r"\\\1", s)


def build(text: str, rating_override: str | None = None) -> str:
    parsed = parse_blocks(text)
    blocks, char_order = parsed["blocks"], parsed["char_order"]
    multi = len(char_order) >= 2

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

    # 公式カードの順: 人数 → 版権キャラ → 作品。
    named = [blocks.get(cid, {}) for cid in char_order if is_named(blocks.get(cid, {}))]
    head.extend(gender_word(cb.get("BASE", "")) for cb in named)
    for cb in named:
        head.extend(tags_of(cb.get("SERIES", "")))
    head = dedup(head)
    action_tags = dedup(action_tags)

    anchors = {cid: char_anchor(blocks.get(cid, {}), inline=True) for cid in char_order}

    # 自然文の補足(位置・方向のある相互干渉)。CAPTION の {CHAR_n} をアンカーへ置換する。
    caption = (blocks.get("NATURAL_LANGUAGE", {}).get("CAPTION", "") or "").strip()
    relation = ""
    if caption and caption.lower() not in NONE_VALUES and char_order:
        def repl(m):
            return anchors.get(f"CHARACTER_{m.group(1)}", m.group(0))
        relation = _CHAR_REF_RE.sub(repl, caption).rstrip(" .!?")

    scene = blocks.get("SCENE_DETAILS", {})
    scene_tags: list[str] = []
    for f in SCENE_ORDER:
        scene_tags.extend(tags_of(scene.get(f, "")))
    scene_tags = dedup(scene_tags)

    seg_lists: list[list[str]] = [head]
    # 概念行: 自然文で各キャラと髪を with で束ねる。全員が版権キャラなら、名前は head に
    # 並んでいるので繰り返さない。
    if multi and len(named) < len(char_order):
        concept = " and ".join(concept_phrase(blocks.get(cid, {})) for cid in char_order)
        seg_lists.append([concept])
    if action_tags:
        seg_lists.append(action_tags)
    for cid in char_order:
        seg_lists.append(char_block(blocks.get(cid, {}), multi))
    if scene_tags:
        seg_lists.append(scene_tags)
    if relation:
        # 自然文はタグ列の最後。dskjal の検証記事:「位置が関係ないタグはプロンプトの頭に
        # 列挙し、位置が重要なタグは自然言語で書く」。
        seg_lists.append([relation])

    positive_line = ", ".join(", ".join(seg) for seg in seg_lists if seg)
    return f"POSITIVE: {escape_parens(positive_line)}\nNEGATIVE: {DEFAULT_NEGATIVE},"


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
