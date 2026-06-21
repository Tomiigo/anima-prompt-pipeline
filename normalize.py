#!/usr/bin/env python3
"""正規化+実在検証: 抽出器が出した構造化ブロックを、辞書で正規化する。

入力 : 抽出器(llama.cpp / Gemma)が出した構造化ブロック(テキスト)。
出力 : 同じブロック構造のまま、各タグを別名解決・正規化したもの。
        これは「組み立て器(assemble.py)への中間データ」であり、
        最終 POSITIVE/NEGATIVE 完成形ではない。
ログ : 実在検証で canonical に無かったタグを別途記録する(削除はしない)。

設計方針:
  - 別名解決は全カテゴリ共通の aliases 一本で引く(表記ゆれはカテゴリ無関係に直す)。
  - 実在検証は canonical で行い、カテゴリ・件数を取得してログ用途に使う。
    実在しないタグも削除せず保持する(エラーにせず通す)。
  - 実在検証の結果は中間データに印を付けず、別ログに分離する
    (組み立て器が中間データを読むため、印で混乱させない)。
  - 照合方向: 辞書キーはアンダースコア区切り。入力タグはスペース→アンダースコアに
    変換して照合し、最終出力でアンダースコア→スペースに戻す。
  - score タグ(score_9 等)のアンダースコアは保持。
  - "none" のフィールドはスキップ。
  - アーティスト(canonical type=artist)には "@" を付与。
  - [NATURAL_LANGUAGE] の CAPTION は辞書置換の対象外(自然文 + {CHAR_n} を保持)。
  - {CHAR_n} 等のプレースホルダを含むトークンは、どのフィールドにあっても
    辞書正規化・実在検証の対象外として原文のまま通す(相互作用タグの
    "{CHAR_3} on {CHAR_2}'s knees" 等を組み立て器へ素通しするため)。

使い方:
  python3 normalize.py --dict booru_dict.json --in extracted.txt \\
      --out normalized.txt --log unresolved.log
  # 標準入力からも受け取れる:
  cat extracted.txt | python3 normalize.py --dict booru_dict.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCORE_RE = re.compile(r"^score_\d+$")


def normalize_key(tag: str) -> str:
    """辞書照合用キー: 小文字化・スペース→アンダースコア。
    score_9 等は既にアンダースコアなのでそのまま残る。"""
    return tag.strip().lower().replace(" ", "_")


def to_output_form(canonical_tag: str) -> str:
    """出力形: アンダースコア→スペース。ただし score タグは保持。"""
    if SCORE_RE.match(canonical_tag):
        return canonical_tag
    return canonical_tag.replace("_", " ")


def resolve_tag(raw_tag: str, aliases: dict, canonical: dict, unresolved: list) -> str | None:
    """1タグを別名解決・正規化する。実在しなければ unresolved に記録するが、
    タグ自体は(別名解決後の形で)返して保持する。None は「捨てる」(none等)。"""
    raw_tag = raw_tag.strip()
    if not raw_tag or raw_tag.lower() == "none":
        return None
    # {CHAR_n} 等のプレースホルダを含むトークンは booru タグではないので、
    # 正規化も実在検証もせず、原文のまま素通しする(CAPTION と同じ扱い)。
    # 相互作用タグ "{CHAR_3} on {CHAR_2}'s knees" 等を組み立て器へそのまま渡すため。
    if "{" in raw_tag and "}" in raw_tag:
        return raw_tag
    key = normalize_key(raw_tag)
    # 別名解決(全カテゴリ共通)。別名なら正規タグへ。
    canon_key = aliases.get(key, key)
    info = canonical.get(canon_key)
    if info is None:
        # 実在検証: canonical に無い。保持するが記録する。
        unresolved.append(canon_key)
        return to_output_form(canon_key)
    out = to_output_form(canon_key)
    if info.get("type") == "artist" and not out.startswith("@"):
        out = "@" + out
    return out


def normalize_field(value: str, aliases: dict, canonical: dict, unresolved: list) -> str:
    """カンマ区切りフィールドを正規化。重複は出現順を保って除去。"""
    seen: set[str] = set()
    out_tags: list[str] = []
    for raw in value.split(","):
        resolved = resolve_tag(raw, aliases, canonical, unresolved)
        if resolved is None:
            continue
        if resolved not in seen:
            seen.add(resolved)
            out_tags.append(resolved)
    return ", ".join(out_tags)


# CAPTION 行(自然文+{CHAR_n})は辞書置換しない
_CAPTION_KEYS = {"CAPTION"}


def process(text: str, aliases: dict, canonical: dict) -> tuple[str, list]:
    """構造化ブロックテキストを行単位で処理し、中間データとログを返す。"""
    unresolved: list[str] = []
    out_lines: list[str] = []
    in_caption_block = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        if stripped.startswith("["):
            in_caption_block = stripped.strip("[]") == "NATURAL_LANGUAGE"
            out_lines.append(line)
            continue
        # "- KEY: value" 形式のフィールド行
        m = re.match(r"^(\s*-\s*)([A-Z_0-9]+)\s*:\s*(.*)$", line)
        if not m:
            out_lines.append(line)
            continue
        prefix, key, value = m.group(1), m.group(2), m.group(3)
        if in_caption_block or key in _CAPTION_KEYS:
            out_lines.append(line)  # CAPTION は触らない
            continue
        new_value = normalize_field(value, aliases, canonical, unresolved)
        out_lines.append(f"{prefix}{key}: {new_value}")

    return "\n".join(out_lines), unresolved


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dict", type=Path, required=True, help="booru_dict.json")
    p.add_argument("--in", dest="infile", type=Path, default=None,
                   help="抽出器の出力テキスト(省略時は標準入力)")
    p.add_argument("--out", type=Path, default=None,
                   help="中間データの出力先(省略時は標準出力)")
    p.add_argument("--log", type=Path, default=None,
                   help="実在検証で未解決だったタグのログ(省略時は標準エラー)")
    args = p.parse_args(argv)

    if not args.dict.exists():
        print(f"エラー: 辞書 {args.dict} が見つかりません。", file=sys.stderr)
        return 1
    db = json.loads(args.dict.read_text(encoding="utf-8"))
    aliases = db.get("aliases", {})
    canonical = db.get("canonical", {})

    text = args.infile.read_text(encoding="utf-8") if args.infile else sys.stdin.read()
    result, unresolved = process(text, aliases, canonical)

    if args.out:
        args.out.write_text(result, encoding="utf-8")
    else:
        print(result)

    # 未解決タグのログ(重複と回数を集計)
    from collections import Counter
    counts = Counter(unresolved)
    log_lines = [f"未解決(canonicalに不在)タグ: {len(counts)} 種 / 延べ {len(unresolved)} 個"]
    for tag, n in counts.most_common():
        log_lines.append(f"  {tag}\t{n}")
    log_text = "\n".join(log_lines) + "\n"
    if args.log:
        args.log.write_text(log_text, encoding="utf-8")
    else:
        sys.stderr.write(log_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
