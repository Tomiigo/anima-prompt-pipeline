#!/usr/bin/env python3
"""Danbooru / Gelbooru の tags + tag_aliases から、変換パイプライン用の辞書を作る。

出力 (booru_dict.json) は二部構成:
  canonical: { tag: {"type": <general|artist|copyright|character|meta>, "count": int} }
             実在検証用。しきい値を生き残った正規タグだけが入る。
  aliases:   { alias: canonical_tag }
             別名解決(表記ゆれ修正)用。実在検証とは切り離し、指す先が
             しきい値以下でも残す(設計判断A: 別名は広く、検証は厳しく)。

設計方針:
  A. 別名解決と実在検証は切り離す。aliasesはcanonicalの生存に依存しない。
  B. カテゴリ衝突はGelbooru優先(綴り・所属ともGelbooruに揃える)。
  C. しきい値: general 30 / artist 45 / copyright 90 / character 90 / meta 1000。
  D. キーはアンダースコア区切りの生CSV形のまま(両サイトとも内部はアンダースコア)。
     置換側がスペース→アンダースコア変換して照合し、最終出力で戻す。
  E. 汚れの除去: 空行、bad_tag集約、自己参照(alias==tag)を弾く。
  F. 出力は canonical / aliases の二部JSON。

カテゴリ対応:
  Danbooru category: 0=general 1=artist 3=copyright 4=character 5=meta
  Gelbooru type:     general / artist / copyright / character / metadata(→meta)

使い方:
  python3 build_booru_dict.py \\
      --danbooru-tags Danbooru_tags.csv --danbooru-aliases Danbooru_tag_aliases.csv \\
      --gelbooru-tags Gelbooru_tags.csv --gelbooru-aliases Gelbooru_tag_aliases.csv \\
      --out booru_dict.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

csv.field_size_limit(10_000_000)

DANBOORU_CAT = {"0": "general", "1": "artist", "3": "copyright", "4": "character", "5": "meta"}
GELBOORU_TYPE = {"general": "general", "artist": "artist", "copyright": "copyright",
                 "character": "character", "metadata": "meta"}
THRESHOLDS = {"general": 30, "artist": 45, "copyright": 90, "character": 90, "meta": 1000}

BAD_TARGETS = {"bad_tag", ""}


def load_gelbooru_tags(path: Path, canonical: dict, stats: Counter) -> None:
    """Gelbooruを先に入れる(カテゴリ衝突時にGelbooruを優先するため)。"""
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            ttype = GELBOORU_TYPE.get((row.get("type") or "").strip().lower())
            if not name or ttype is None:
                stats["gelbooru_skipped_type"] += 1
                continue
            try:
                count = int(row.get("count") or 0)
            except ValueError:
                count = 0
            if count >= THRESHOLDS[ttype]:
                canonical[name] = {"type": ttype, "count": count}
                stats[f"gelbooru_kept_{ttype}"] += 1
            else:
                stats[f"gelbooru_dropped_{ttype}"] += 1


def load_danbooru_tags(path: Path, canonical: dict, stats: Counter) -> None:
    """Danbooruを後に入れる。設計判断B: 既にGelbooru由来のキーがあれば上書きしない。"""
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("is_deprecated") or "").lower() == "true":
                stats["danbooru_deprecated"] += 1
                continue
            name = (row.get("name") or "").strip()
            ttype = DANBOORU_CAT.get((row.get("category") or "").strip())
            if not name or ttype is None:
                stats["danbooru_skipped_cat"] += 1
                continue
            try:
                count = int(row.get("post_count") or 0)
            except ValueError:
                count = 0
            if count < THRESHOLDS[ttype]:
                stats[f"danbooru_dropped_{ttype}"] += 1
                continue
            if name in canonical:
                stats["danbooru_yielded_to_gelbooru"] += 1
            else:
                canonical[name] = {"type": ttype, "count": count}
                stats[f"danbooru_kept_{ttype}"] += 1


def load_aliases(path: Path, aliases: dict, stats: Counter) -> None:
    """別名表を読む。設計判断A: canonicalの生存に依存しない。
    設計判断E: 空・bad_tag・自己参照を弾く。先に入れた方(Gelbooru)を衝突時優先。"""
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            alias = (row.get("alias") or "").strip()
            tag = (row.get("tag") or "").strip()
            if not alias or tag in BAD_TARGETS:
                stats["alias_skipped_dirty"] += 1
                continue
            if alias == tag:
                stats["alias_skipped_selfref"] += 1
                continue
            if alias in aliases:
                stats["alias_collision_kept_existing"] += 1
                continue
            aliases[alias] = tag
            stats["alias_added"] += 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--danbooru-tags", type=Path, required=True)
    p.add_argument("--danbooru-aliases", type=Path, required=True)
    p.add_argument("--gelbooru-tags", type=Path, required=True)
    p.add_argument("--gelbooru-aliases", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("booru_dict.json"))
    args = p.parse_args(argv)

    for f in [args.danbooru_tags, args.danbooru_aliases,
              args.gelbooru_tags, args.gelbooru_aliases]:
        if not f.exists():
            print(f"エラー: {f} が見つかりません。", file=sys.stderr)
            return 1

    canonical: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    stats: Counter = Counter()

    # tags: Gelbooru先 → Danbooru後(B: カテゴリ衝突はGelbooru優先)
    load_gelbooru_tags(args.gelbooru_tags, canonical, stats)
    load_danbooru_tags(args.danbooru_tags, canonical, stats)
    # aliases: Gelbooru先 → Danbooru後(衝突時Gelbooru優先)
    load_aliases(args.gelbooru_aliases, aliases, stats)
    load_aliases(args.danbooru_aliases, aliases, stats)

    alias_to_survivor = sum(1 for t in aliases.values() if t in canonical)
    alias_to_dropped = len(aliases) - alias_to_survivor

    args.out.write_text(json.dumps(
        {"canonical": canonical, "aliases": aliases},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    cat_counter: Counter = Counter(v["type"] for v in canonical.values())
    print(f"出力: {args.out.resolve()}")
    print(f"\n実在検証集合 (canonical): {len(canonical):,} タグ")
    for c in ["general", "artist", "copyright", "character", "meta"]:
        print(f"  {c}: {cat_counter.get(c, 0):,}")
    print(f"\n別名解決 (aliases): {len(aliases):,} 件")
    print(f"  指す先が実在検証集合内: {alias_to_survivor:,}")
    print(f"  指す先がしきい値以下(切り離しにより保持): {alias_to_dropped:,}")
    print(f"\n[内訳カウンタ]")
    for k in sorted(stats):
        print(f"  {k}: {stats[k]:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
