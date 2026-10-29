#!/usr/bin/env python3
"""Danbooru(と任意で Gelbooru)のタグ・別名から、変換パイプライン用の辞書を作る。

出力 (booru_dict.json, format 2):
  canonical:  { tag: [type, count] }
              type は general / character / copyright / meta。実在検証と、
              キャラ・作品タグの判定に使う。**アーティストのタグは入れない**(設計判断G)。
  aliases:    { alias: canonical_tag }
              別名解決(表記ゆれ修正)用。連鎖は最終の正規タグまで解いてある。
  deprecated: [ tag, ... ]
              Danbooru で廃止され、転送先も無いタグ。正規化で未解決として扱う。
  characters: { character_tag: {"series": [...], "hair": [...], "eyes": [...],
                                "body": [...], "outfit": [...]} }
              版権キャラ → 既定の特徴。正規化の段で、文章が触れていない部位だけを補う。
              --cooccurrence か --characters を渡したときだけ作る。

設計方針:
  A. Danbooru を正本にする。Danbooru は全タグ(約160万)を持ち、カテゴリも正確。
     Gelbooru は Danbooru に無い名前を足すだけ(Gelbooru の deepghs 版は上位約2万件しか
     無く、Danbooru とカテゴリが食い違う例も多いため、優先させない)。
  B. 件数しきい値でタグを捨てない。投稿が --min-count 件以上なら実在として残す
     (旧版は character/copyright を 90 件未満で捨てており、版権キャラの大半が消えていた)。
     件数は残すので、少ないタグは正規化のログで「低頻度」として知らせる。
  C. 別名の転送元が、それ自体いま生きている正規タグなら、転送しない
     (旧版は実在するタグを実在しないタグへ 830 件転送していた。例 blue_socks)。
  D. 別名の連鎖(a→b→c)は最終の正規タグまで解く。循環や、行き先が辞書に無い別名は捨てる。
  E. 汚れの除去: 空行、bad_tag 集約、自己参照(alias==tag)を弾く。
  F. キーはアンダースコア区切り(両サイトとも内部はアンダースコア)。
     置換側がスペース→アンダースコア変換して照合し、最終出力で戻す。
  G. アーティストのタグは辞書に入れない。このツールはアーティストを扱わない設計。
     アーティストを指す別名も捨てる。

キャラの特徴の出所:
  --characters    Minthy/BooruCharacters の parquet(popular_tags 列)。第一の出所。
                  読むには pyarrow が要る(このビルド段だけ)。
  --cooccurrence  Danbooru のタグ共起表(tag_a,tag_b,count)。キャラの投稿のうち一定の
                  割合以上に付いているタグで補う。服は --outfit-ratio、髪・目・身体・作品は
                  --identity-ratio 以上(同じ絵の別キャラの特徴が混ざるのを抑えるため高め)。
  特徴は語形で部位に振り分ける(髪・目・身体・服)。体型・胸の大きさ・性的なタグ・
  構図や背景のような「キャラ固有でない」タグは入れない。

カテゴリ対応:
  Danbooru category: 0=general 1=artist 3=copyright 4=character 5=meta
  Gelbooru type:     general / artist / copyright / character / metadata(→meta)

使い方:
  python3 tools/build_booru_dict.py \\
      --danbooru-tags Danbooru_tags.csv --danbooru-aliases Danbooru_tag_aliases.csv \\
      [--gelbooru-tags Gelbooru_tags.csv --gelbooru-aliases Gelbooru_tag_aliases.csv] \\
      [--cooccurrence danbooru_tags_cooccurrence.csv] [--characters booru_characters.parquet] \\
      --out booru_dict.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

csv.field_size_limit(10_000_000)

DANBOORU_CAT = {"0": "general", "1": "artist", "3": "copyright", "4": "character", "5": "meta"}
GELBOORU_TYPE = {"general": "general", "artist": "artist", "copyright": "copyright",
                 "character": "character", "metadata": "meta"}
KEPT_TYPES = {"general", "copyright", "character", "meta"}  # 設計判断G: artist は入れない

BAD_TARGETS = {"bad_tag", ""}
MAX_ALIAS_HOPS = 10

# ---- キャラの特徴を部位へ振り分ける語形の規則 -------------------------------------
# どのデータにも「髪」「服」のような細分類は無いため、語形で判定する。
# 当てはまらないタグ(表情・構図・背景・体型・性的なタグなど)は特徴として使わない。
_HAIR_ACCESSORY_RE = re.compile(
    r"(^|_)(hair_ornament|hairband|hair_band|hairclip|hair_clip|hair_bow|hair_ribbon|"
    r"hair_flower|hairpin|hair_bobbles|hair_tubes|hair_rings?|hair_tie|hair_scrunchie|"
    r"hair_beads|hair_stick|hair_bun_cover)s?$")
_HAIR_RE = re.compile(
    r"(^|_)hair$|(^|_)(twintails|ponytail|side_ponytail|braid|braids|twin_braids|bangs|"
    r"sidelocks|ahoge|bob_cut|hime_cut|hair_bun|double_bun|single_hair_bun|drill_hair|"
    r"ringlets|hair_over_one_eye|hair_between_eyes|hair_intakes|streaked_hair|"
    r"multicolored_hair|gradient_hair|two-tone_hair|colored_inner_hair)$")
_EYES_RE = re.compile(r"(^|_)eyes$|^heterochromia$|(^|_)pupils$|^ringed_eyes$")
_EYES_NOT_RE = re.compile(r"^(closed_eyes|half-closed_eyes|one_eye_closed|wide-eyed|"
                          r"rolling_eyes|empty_eyes|crazy_eyes|glowing_eyes|tearing_up)$")
_BODY_RE = re.compile(
    r"(^|_)(ears|horns?|wings|tail|halo|fangs?|fang_out|freckles|mole|"
    r"mole_under_eye|mole_under_mouth|scar|scar_on_face|dark_skin|dark-skinned_female|"
    r"dark-skinned_male|pale_skin|colored_skin|blue_skin|grey_skin|green_skin|red_skin|"
    r"elf|dark_elf|pointy_ears|animal_ears|cat_ears|fox_ears|dog_ears|wolf_ears|"
    r"rabbit_ears|horse_ears|mechanical_arms|prosthesis|robot_joints|android|cyborg)$")
_SPECIES_RE = re.compile(r"^[a-z]+_(girl|boy)$")  # fox_girl, cat_boy など(girl/boy 単体は除く)
_OUTFIT_RE = re.compile(
    r"(^|_)(dress|shirt|skirt|jacket|coat|uniform|serafuku|hoodie|sweater|vest|cape|"
    r"capelet|cloak|robe|kimono|yukata|hakama|haori|obi|apron|armor|armour|gauntlets|"
    r"gloves|boots|shoes|sandals|loafers|heels|thighhighs|kneehighs|pantyhose|socks|"
    r"leggings|shorts|pants|jeans|trousers|leotard|bodysuit|necktie|tie|bowtie|ascot|"
    r"neckerchief|ribbon|bow|hat|cap|beret|helmet|crown|tiara|headband|headphones|"
    r"headset|headgear|headpiece|earrings|necklace|choker|scarf|belt|bracelet|glasses|"
    r"eyewear|goggles|mask|collar|sleeves|detached_sleeves|corset|cardigan|blazer|"
    r"tabard|tunic|overalls|suit|tuxedo|veil|brooch|pendant|jewelry|sash|shawl|poncho|"
    r"wristband|armband|hairband|hair_ornament|hairclip|hair_bow|hair_ribbon|hair_flower|"
    r"hairpin|x_hair_ornament|zettai_ryouiki|sleeveless|bare_shoulders|off_shoulder|"
    r"long_sleeves|short_sleeves|wide_sleeves|puffy_sleeves|frills|frilled_dress|"
    r"pleated_skirt|miniskirt|thigh_boots|knee_boots|cross-laced_footwear)$")
# 服の語でも、下着・露出・性的な語は補わない(文章が求めていないものを足さないため)。
_OUTFIT_EXCLUDE_RE = re.compile(
    r"(^|_)(panties|bra|lingerie|underwear|nude|naked|nipples?|pussy|penis|cleavage|"
    r"underboob|sideboob|bikini|swimsuit|micro|see-through|torn|wet)(_|$)")


def classify_feature(tag: str) -> str | None:
    """キャラの特徴タグを部位(hair/eyes/body/outfit)へ振り分ける。使わないタグは None。"""
    if _OUTFIT_EXCLUDE_RE.search(tag):
        return None
    if _HAIR_ACCESSORY_RE.search(tag):
        return "outfit"
    if _HAIR_RE.search(tag):
        return "hair"
    if _EYES_RE.search(tag) and not _EYES_NOT_RE.match(tag):
        return "eyes"
    if _BODY_RE.search(tag) or _SPECIES_RE.match(tag):
        return "body"
    if _OUTFIT_RE.search(tag):
        return "outfit"
    return None


FEATURE_LIMITS = {"series": 2, "hair": 5, "eyes": 2, "body": 4, "outfit": 12}


# ---- タグ表 -----------------------------------------------------------------------

def load_danbooru_tags(path: Path, min_count: int, canonical: dict, deprecated: set,
                       artists: set, stats: Counter) -> None:
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            ttype = DANBOORU_CAT.get((row.get("category") or "").strip())
            if not name or ttype is None:
                stats["danbooru_skipped_cat"] += 1
                continue
            if ttype == "artist":
                artists.add(name)
                stats["danbooru_artist_excluded"] += 1
                continue
            if (row.get("is_deprecated") or "").lower() == "true":
                deprecated.add(name)
                stats["danbooru_deprecated"] += 1
                continue
            try:
                count = int(row.get("post_count") or 0)
            except ValueError:
                count = 0
            if count < min_count:
                stats[f"danbooru_below_min_{ttype}"] += 1
                continue
            canonical[name] = [ttype, count]
            stats[f"danbooru_kept_{ttype}"] += 1


def load_gelbooru_tags(path: Path, min_count: int, canonical: dict, deprecated: set,
                       artists: set, stats: Counter) -> None:
    """設計判断A: Danbooru に無い名前だけを足す。
    Danbooru で廃止されたタグでも、Gelbooru で使われ続けていれば実在として戻す
    (例 duo。Danbooru は廃止したが、学習データには大量に残っている)。"""
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            ttype = GELBOORU_TYPE.get((row.get("type") or "").strip().lower())
            if not name or ttype is None:
                stats["gelbooru_skipped_type"] += 1
                continue
            if ttype == "artist":
                artists.add(name)
                stats["gelbooru_artist_excluded"] += 1
                continue
            if name in canonical or name in artists:
                stats["gelbooru_already_known"] += 1
                continue
            try:
                count = int(row.get("count") or 0)
            except ValueError:
                count = 0
            if count < min_count:
                stats[f"gelbooru_below_min_{ttype}"] += 1
                continue
            canonical[name] = [ttype, count]
            if name in deprecated:
                deprecated.discard(name)
                stats[f"gelbooru_restored_danbooru_deprecated_{ttype}"] += 1
            else:
                stats[f"gelbooru_added_{ttype}"] += 1


# ---- 別名 -------------------------------------------------------------------------

def load_alias_pairs(path: Path, pairs: dict, stats: Counter, label: str) -> None:
    """設計判断E: 空・bad_tag・自己参照を弾く。先に読んだ方(Danbooru)を衝突時優先。"""
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            alias = (row.get("alias") or "").strip()
            tag = (row.get("tag") or "").strip()
            if not alias or tag in BAD_TARGETS:
                stats[f"{label}_alias_skipped_dirty"] += 1
                continue
            if alias == tag:
                stats[f"{label}_alias_skipped_selfref"] += 1
                continue
            if alias in pairs:
                stats[f"{label}_alias_collision_kept_existing"] += 1
                continue
            pairs[alias] = tag


def resolve_aliases(pairs: dict, canonical: dict, stats: Counter) -> dict:
    """設計判断C・D: 生きている正規タグは転送しない。連鎖を解き、行き先が無い別名は捨てる。"""
    aliases: dict[str, str] = {}
    for alias, target in pairs.items():
        if alias in canonical:
            stats["alias_source_is_canonical_kept_as_is"] += 1
            continue
        seen = {alias}
        cur = target
        hops = 0
        while cur not in canonical and cur in pairs and hops < MAX_ALIAS_HOPS:
            if cur in seen:
                break
            seen.add(cur)
            cur = pairs[cur]
            hops += 1
        if cur in canonical:
            aliases[alias] = cur
            stats["alias_added" if hops == 0 else "alias_added_via_chain"] += 1
        else:
            stats["alias_dropped_target_unknown"] += 1
    return aliases


# ---- キャラの特徴 -----------------------------------------------------------------

def _add_feature(entry: dict, slot: str, tag: str) -> None:
    lst = entry.setdefault(slot, [])
    if tag not in lst and len(lst) < FEATURE_LIMITS[slot]:
        lst.append(tag)


IDENTITY_SLOTS = {"hair", "eyes", "body"}
COMPANION_RATIO = 0.15  # このキャラの投稿の 15% 以上に一緒に描かれる別キャラを同伴キャラとする

_COLORS = ("aqua|black|blonde|blue|brown|green|grey|gray|orange|pink|purple|red|silver|"
           "white|yellow|light_brown|light_blue|light_purple|dark_blue|dark_green|"
           "multicolored|two-tone|gradient|streaked")
_COLOR_RE = re.compile(rf"^({_COLORS})_(hair|eyes)$")


def _is_color(tag: str) -> bool:
    return bool(_COLOR_RE.match(tag))


def load_cooccurrence(path: Path, canonical: dict, identity_ratio: float, outfit_ratio: float,
                      characters: dict, stats: Counter) -> None:
    """キャラの投稿のうち一定の割合以上に付いている一般タグ・作品タグを既定の特徴とする。

    共起は「同じ絵に一緒に描かれた別キャラ」の特徴も拾う(実測: fern_(sousou_no_frieren)
    の投稿の 33% に elf が付くが、これは同じ絵の frieren の特徴)。そのため
    同伴キャラ(COMPANION_RATIO 以上に一緒に描かれるキャラ)のほうがそのタグを高い割合で
    持つなら、そのタグは同伴キャラの特徴とみなして取らない。髪・目・身体・作品は
    identity_ratio 以上、服は絵ごとに変わるので低めの outfit_ratio 以上で拾う。
    髪・目の色は1つに留める。"""
    rows: dict[str, list[tuple[float, str]]] = defaultdict(list)
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a, b = (row.get("tag_a") or "").strip(), (row.get("tag_b") or "").strip()
            try:
                n = float(row.get("count") or 0)
            except ValueError:
                continue
            # 共起表は片方向にしか載っていないので、両方向を見る。
            for ch, other in ((a, b), (b, a)):
                info = canonical.get(ch)
                if info and info[0] == "character":
                    rows[ch].append((n, other))
    # キャラごとの「タグ → 割合」。同伴キャラとの比較に使う。
    ratios: dict[str, dict[str, float]] = {}
    for ch, pairs in rows.items():
        total = canonical[ch][1]
        if total > 0:
            ratios[ch] = {other: n / total for n, other in pairs}

    low = min(identity_ratio, outfit_ratio)
    for ch, r_of in ratios.items():
        # 同じ絵によく一緒に描かれる別キャラ(同伴キャラ)。
        companions = [c for c, r in r_of.items()
                      if r >= COMPANION_RATIO and canonical.get(c, [""])[0] == "character"]
        entry = characters.setdefault(ch, {})
        added = False
        for other, r in sorted(r_of.items(), key=lambda kv: -kv[1]):
            if r < low:
                break
            info = canonical.get(other)
            if not info:
                continue
            if info[0] == "copyright":
                if r >= identity_ratio:
                    _add_feature(entry, "series", other)
                    added = True
                continue
            if info[0] != "general":
                continue
            slot = classify_feature(other)
            if not slot or r < (outfit_ratio if slot == "outfit" else identity_ratio):
                continue
            # 同伴キャラのほうがこのタグを強く持つなら、それは同伴キャラの特徴とみなす。
            if any(ratios.get(c, {}).get(other, 0.0) > r for c in companions):
                stats["cooccurrence_skipped_companion_trait"] += 1
                continue
            if _is_color(other) and any(_is_color(t) for t in entry.get(slot, [])):
                continue  # 髪・目の色は1つに留める(Minthy の色を優先)
            _add_feature(entry, slot, other)
            added = True
        if added:
            stats["characters_from_cooccurrence"] += 1


def load_minthy(path: Path, canonical: dict, characters: dict, stats: Counter) -> None:
    """Minthy/BooruCharacters の popular_tags を、キャラの特徴の第一の出所にする。
    共起表より先に読む(同じ絵の別キャラの特徴が混ざりにくいため)。"""
    try:
        import pyarrow.parquet as pq  # このビルド段だけの依存
    except ImportError:
        print("エラー: --characters の parquet を読むには pyarrow が要ります "
              "(pip install pyarrow)。", file=sys.stderr)
        raise SystemExit(1)
    table = pq.read_table(path, columns=["tag", "type", "popular_tags"]).to_pylist()
    for row in table:
        ch = row.get("tag")
        if row.get("type") != "character" or not ch:
            continue
        info = canonical.get(ch)
        if not info or info[0] != "character":
            continue
        entry = characters.setdefault(ch, {})
        for t in row.get("popular_tags") or []:
            tinfo = canonical.get(t)
            if not tinfo:
                continue
            if tinfo[0] == "copyright":
                _add_feature(entry, "series", t)
            elif tinfo[0] == "general":
                slot = classify_feature(t)
                if slot:
                    _add_feature(entry, slot, t)
        if entry:
            stats["characters_from_minthy"] += 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--danbooru-tags", type=Path, required=True)
    p.add_argument("--danbooru-aliases", type=Path, required=True)
    p.add_argument("--gelbooru-tags", type=Path, default=None)
    p.add_argument("--gelbooru-aliases", type=Path, default=None)
    p.add_argument("--cooccurrence", type=Path, default=None,
                   help="Danbooru のタグ共起表(tag_a,tag_b,count)。キャラの既定の特徴に使う")
    p.add_argument("--characters", type=Path, default=None,
                   help="Minthy/BooruCharacters の parquet。キャラの特徴を補う(pyarrow が要る)")
    p.add_argument("--min-count", type=int, default=1,
                   help="実在とみなす最小の投稿数(既定 1)")
    p.add_argument("--identity-ratio", type=float, default=0.3,
                   help="共起表から髪・目・身体・作品を取るときの最小の割合(既定 0.3)")
    p.add_argument("--outfit-ratio", type=float, default=0.2,
                   help="共起表から服を取るときの最小の割合(既定 0.2)")
    p.add_argument("--out", type=Path, default=Path("booru_dict.json"))
    args = p.parse_args(argv)

    inputs = [args.danbooru_tags, args.danbooru_aliases, args.gelbooru_tags,
              args.gelbooru_aliases, args.cooccurrence, args.characters]
    for f in inputs:
        if f is not None and not f.exists():
            print(f"エラー: {f} が見つかりません。", file=sys.stderr)
            return 1

    canonical: dict[str, list] = {}
    deprecated: set[str] = set()
    artists: set[str] = set()
    stats: Counter = Counter()

    load_danbooru_tags(args.danbooru_tags, args.min_count, canonical, deprecated, artists, stats)
    if args.gelbooru_tags:
        load_gelbooru_tags(args.gelbooru_tags, args.min_count, canonical, deprecated,
                           artists, stats)

    pairs: dict[str, str] = {}
    load_alias_pairs(args.danbooru_aliases, pairs, stats, "danbooru")
    if args.gelbooru_aliases:
        load_alias_pairs(args.gelbooru_aliases, pairs, stats, "gelbooru")
    aliases = resolve_aliases(pairs, canonical, stats)
    # 廃止タグのうち転送先があるものは別名で解けるので、廃止の一覧からは外す。
    deprecated_left = sorted(t for t in deprecated if t not in aliases and t not in canonical)

    characters: dict[str, dict] = {}
    if args.characters:
        load_minthy(args.characters, canonical, characters, stats)
    if args.cooccurrence:
        load_cooccurrence(args.cooccurrence, canonical, args.identity_ratio, args.outfit_ratio,
                          characters, stats)
    characters = {k: v for k, v in characters.items() if v}

    args.out.write_text(json.dumps(
        {"format": 2, "canonical": canonical, "aliases": aliases,
         "deprecated": deprecated_left, "characters": characters},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    cat_counter: Counter = Counter(v[0] for v in canonical.values())
    print(f"出力: {args.out.resolve()}")
    print(f"\n実在検証集合 (canonical): {len(canonical):,} タグ (アーティストは除外)")
    for c in ["general", "character", "copyright", "meta"]:
        print(f"  {c}: {cat_counter.get(c, 0):,}")
    print(f"\n別名解決 (aliases): {len(aliases):,} 件")
    print(f"廃止タグ (転送先なし): {len(deprecated_left):,} 件")
    print(f"キャラの既定の特徴: {len(characters):,} キャラ")
    print(f"\n[内訳カウンタ]")
    for k in sorted(stats):
        print(f"  {k}: {stats[k]:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
