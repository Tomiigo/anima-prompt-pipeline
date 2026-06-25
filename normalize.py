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

HERE = Path(__file__).resolve().parent
SCORE_RE = re.compile(r"^score_\d+$")


def load_interaction_map(path: Path | None) -> dict:
    """相互干渉の写像表(interaction_map.json)を読む。無ければ空(従来動作)。"""
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("map", {})


def expand_interaction(value: str, imap: dict, notes: list) -> str:
    """[INTERACTION] COUNT_AND_RELATION 専用: 既知の干渉表現を信頼できる正準タグへ
    確定変換する。未知トークンは原文のまま通し、後段の別名解決・実在検証に委ねる。
    変換したものは notes に (元表現, 変換後タグ, 信頼度) を記録する。"""
    out: list[str] = []
    for raw in value.split(","):
        tok = raw.strip()
        if not tok:
            continue
        entry = imap.get(tok.lower())
        if entry:
            tags = entry.get("tags", [])
            conf = entry.get("confidence", "unknown")
            notes.append((tok, tags, conf))
            out.extend(tags)
        else:
            out.append(tok)
    return ", ".join(out)


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


def process(text: str, aliases: dict, canonical: dict, imap: dict | None = None) -> tuple[str, list, list]:
    """構造化ブロックテキストを行単位で処理し、中間データ・未解決ログ・干渉ノートを返す。"""
    imap = imap or {}
    unresolved: list[str] = []
    interaction_notes: list = []
    out_lines: list[str] = []
    current_block = ""
    in_caption_block = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        if stripped.startswith("["):
            current_block = stripped.strip("[]")
            in_caption_block = current_block == "NATURAL_LANGUAGE"
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
        # 相互干渉の写像表は INTERACTION の COUNT_AND_RELATION だけに効かせる
        if current_block == "INTERACTION" and key == "COUNT_AND_RELATION" and imap:
            value = expand_interaction(value, imap, interaction_notes)
        new_value = normalize_field(value, aliases, canonical, unresolved)
        out_lines.append(f"{prefix}{key}: {new_value}")

    return "\n".join(out_lines), unresolved, interaction_notes


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
    p.add_argument("--interaction-map", type=Path, default=HERE / "interaction_map.json",
                   help="相互干渉の写像表(既定: スクリプトと同じ場所の interaction_map.json)")
    args = p.parse_args(argv)

    if not args.dict.exists():
        print(f"エラー: 辞書 {args.dict} が見つかりません。", file=sys.stderr)
        return 1
    db = json.loads(args.dict.read_text(encoding="utf-8"))
    aliases = db.get("aliases", {})
    canonical = db.get("canonical", {})
    imap = load_interaction_map(args.interaction_map)

    text = args.infile.read_text(encoding="utf-8") if args.infile else sys.stdin.read()
    result, unresolved, interaction_notes = process(text, aliases, canonical, imap)

    if args.out:
        args.out.write_text(result, encoding="utf-8")
    else:
        print(result)

    # 相互干渉の写像ノート(変換内容と信頼度)を標準エラーに出す
    if interaction_notes:
        print("[相互干渉] 写像表で確定変換:", file=sys.stderr)
        low = []
        for src, tags, conf in interaction_notes:
            print(f"  \"{src}\" -> {', '.join(tags)}  [{conf}]", file=sys.stderr)
            if conf == "low":
                low.append(src)
        if low:
            print(f"  注意: 低信頼(low)の干渉 {low} は、画像生成で意図通り出ない可能性があります。",
                  file=sys.stderr)
    elif imap:
        print("[相互干渉] 写像表に該当する干渉表現はありませんでした。", file=sys.stderr)
    else:
        print("[相互干渉] 写像表が読み込めず(無効)、干渉変換はスキップしました。", file=sys.stderr)

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
