#!/usr/bin/env python3
"""正規化+実在検証: 抽出器が出した構造化ブロックを、辞書で正規化する。

入力 : 抽出器(llama.cpp / Gemma)が出した構造化ブロック(テキスト)。
出力 : 同じブロック構造のまま、各タグを別名解決・正規化したもの。
        これは「組み立て器(assemble.py)への中間データ」であり、
        最終 POSITIVE/NEGATIVE 完成形ではない。
ログ : 辞書に無かったタグ(と、その候補)、自動で置き換えたタグ、低頻度のタグ、
        キャラの既定の特徴で補ったタグを別途記録する。

設計方針:
  - 辞書は tools/build_booru_dict.py が作る format 2(canonical / aliases / deprecated /
    characters)。アーティストのタグは辞書に無く、このツールは扱わない。
  - 照合方向: 辞書キーはアンダースコア区切り。入力タグはスペース→アンダースコアに
    変換して照合し、最終出力でアンダースコア→スペースに戻す。score_9 等は保持。
  - それ自体が実在するタグは別名で転送しない(実在するタグを実在しないタグへ化けさせないため)。
  - 辞書に無いタグは次の順で救う。救えなければ削除せず保持し、ログに候補を出す:
      1. 同義語表(tag_synonyms.json)   例 high angle -> from above
      2. 表記ゆれ(ハイフン・複数形・語順) 例 pony tail -> ponytail
      3. 綴りが近い実在タグは、候補としてログに出すだけ(意味が違うことがあるため)
  - "none" や "none visible" のような「無し」を表す値は捨てる。
  - [NATURAL_LANGUAGE] の CAPTION は辞書置換の対象外(自然文 + {CHAR_n} を保持)。
  - {CHAR_n} 等のプレースホルダを含むトークンは、どのフィールドにあっても
    原文のまま通す(相互作用タグの "{CHAR_3} on {CHAR_2}'s knees" 等を組み立て器へ渡すため)。
  - BASE が版権キャラなら、正規のキャラタグへ直し、作品タグを "- SERIES:" 行に置く。
    名前だけで同名のキャラが複数あるときは、BASE に添えた作品名、無ければ投稿数で選ぶ。
  - 版権キャラの既定の特徴を補う。髪・目は、文章がその部位に触れていないときだけ補う。
    身体の特徴(耳・角・尻尾など)は無いものだけ足す。服は OUTFIT が空のときだけ補う
    (服のタグが無いと Anima は服を描かないため)。

使い方:
  python3 normalize.py --dict booru_dict.json --in extracted.txt \\
      --out normalized.txt --log unresolved.log
  # 標準入力からも受け取れる:
  cat extracted.txt | python3 normalize.py --dict booru_dict.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCORE_RE = re.compile(r"^score_\d+$")
COUNT_RE = re.compile(r"^(\d+(girl|boy|other)s?|multiple_(girls|boys|others)|solo|duo|trio|group)$")
QUALIFIER_RE = re.compile(r"^(.*?)_\(([^()]*)\)$")

# 別名解決も実在検証もかけず、書いたとおりに通す語。
#   Anima は booru タグで学習されており、成人の女性と男性が1人ずつ描かれた絵でも
#   "1girl, 1boy" とタグ付けされている。そのため人物の性別は female / male で表す。
#   辞書を通すと壊れる: male -> male_focus(画面が男性主体という構図のタグで、
#   個人の性別ではない)、adult -> aged_up(原作より年上に描いたという意味)。
LITERAL_TAGS = {"female", "male", "other", "adult"}

# 「無し」を表す値。タグとして出力しない。
NONE_VALUES = {"none", "none visible", "not visible", "n/a", "na", "unknown", "unspecified",
               "nothing", "no outfit", "not applicable"}

# 投稿がこれより少ないタグは、Anima が覚えていない可能性があるので「低頻度」として知らせる
# (旧版の辞書が捨てていたしきい値。いまは捨てずに知らせるだけ)。
LOW_COUNT = {"general": 30, "character": 90, "copyright": 90, "meta": 1000}

FUZZY_CANDIDATE = 0.75   # 綴りがこれ以上近い実在タグを、候補としてログに出す

_HAIR_SLOT_RE = re.compile(r"hair\b|\b(twintails|ponytail|braids?|bangs|sidelocks|ahoge|bun|"
                           r"buns|bob cut|hime cut|drill hair|ringlets|afro)\b")
_EYES_SLOT_RE = re.compile(r"\beyes\b|\bheterochromia\b|\bpupils\b")


def normalize_key(tag: str) -> str:
    """辞書照合用キー: 小文字化・スペース→アンダースコア。"""
    return tag.strip().lower().replace(" ", "_")


def to_output_form(canonical_tag: str) -> str:
    """出力形: アンダースコア→スペース。ただし score タグは保持。"""
    if SCORE_RE.match(canonical_tag):
        return canonical_tag
    return canonical_tag.replace("_", " ")


def is_none(value: str) -> bool:
    return value.strip().lower() in NONE_VALUES


class BooruDict:
    """booru_dict.json(format 2)の引き当て。索引は必要になったときだけ作る。"""

    def __init__(self, data: dict):
        if data.get("format") != 2:
            raise ValueError("booru_dict.json が古い形式です。tools/build_booru_dict.py で"
                             "作り直してください(docs/dictionary.md)。")
        self.canonical: dict[str, list] = data.get("canonical", {})
        self.aliases: dict[str, str] = data.get("aliases", {})
        self.deprecated: set[str] = set(data.get("deprecated", []))
        self.characters: dict[str, dict] = data.get("characters", {})
        self._word_index: dict[str, list[str]] | None = None
        self._char_base: dict[str, list[str]] | None = None
        self._char_token: dict[str, list[str]] | None = None

    def lookup(self, key: str) -> str | None:
        """実在タグならそのまま、別名なら転送先を返す。無ければ None。"""
        if key in self.canonical:
            return key
        return self.aliases.get(key)

    def type_of(self, key: str) -> str | None:
        info = self.canonical.get(key)
        return info[0] if info else None

    def count_of(self, key: str) -> int:
        info = self.canonical.get(key)
        return info[1] if info else 0

    def general_word_index(self) -> dict[str, list[str]]:
        if self._word_index is None:
            idx: dict[str, list[str]] = defaultdict(list)
            for tag, (ttype, _count) in self.canonical.items():
                if ttype != "general":
                    continue
                for w in set(tag.split("_")):
                    if len(w) >= 2:
                        idx[w].append(tag)
            self._word_index = idx
        return self._word_index

    def _build_char_indexes(self) -> None:
        base: dict[str, list[str]] = defaultdict(list)
        token: dict[str, list[str]] = defaultdict(list)
        for tag, (ttype, _count) in self.canonical.items():
            if ttype != "character":
                continue
            m = QUALIFIER_RE.match(tag)
            name = m.group(1) if m else tag
            base[name].append(tag)
            for w in set(name.split("_")):
                if len(w) >= 2:
                    token[w].append(tag)
        self._char_base, self._char_token = base, token

    def character_candidates(self, key: str) -> list[str]:
        """"fern" -> fern_(sousou_no_frieren) のように、名前からキャラタグの候補を引く。"""
        if self._char_base is None:
            self._build_char_indexes()
        found = list(self._char_base.get(key, []))
        if not found and "_" not in key:
            found = list(self._char_token.get(key, []))
        return sorted(set(found), key=lambda t: -self.count_of(t))


class Normalizer:
    def __init__(self, bdict: BooruDict, synonyms: dict | None = None):
        self.d = bdict
        self.synonyms = {k.lower(): v for k, v in (synonyms or {}).items()}
        self.unresolved: list[tuple[str, list[str]]] = []
        self.replaced: list[tuple[str, str, str]] = []
        self.rare: list[tuple[str, int]] = []
        self.filled: list[tuple[str, str, list[str]]] = []

    # ---- 1タグ ------------------------------------------------------------------
    def _accept(self, key: str) -> str:
        ttype = self.d.type_of(key)
        if ttype and self.d.count_of(key) < LOW_COUNT.get(ttype, 0):
            self.rare.append((key, self.d.count_of(key)))
        return to_output_form(key)

    def _variants(self, key: str) -> list[str]:
        out: list[str] = []
        if "-" in key:
            out.append(key.replace("-", "_"))
        if "_" in key:
            out.append(key.replace("_", ""))       # pony_tail -> ponytail
            parts = key.split("_")
            if len(parts) == 2:
                out.append(f"{parts[1]}_{parts[0]}")
        if key.endswith("es"):
            out.append(key[:-2])
        if key.endswith("s"):
            out.append(key[:-1])
        else:
            out.append(key + "s")
        return out

    def _fuzzy(self, key: str) -> list[tuple[float, str]]:
        idx = self.d.general_word_index()
        pool: set[str] = set()
        for w in set(key.split("_")):
            if len(w) >= 2:
                # 語ごとに投稿数の多い順で上限を設け、照合の手間を抑える。
                pool.update(sorted(idx.get(w, []), key=lambda t: -self.d.count_of(t))[:400])
        scored = []
        for t in pool:
            r = difflib.SequenceMatcher(None, key, t).ratio()
            if r >= FUZZY_CANDIDATE:
                scored.append((r, t))
        scored.sort(key=lambda x: (-x[0], -self.d.count_of(x[1])))
        return scored

    def resolve_tag(self, raw_tag: str) -> str | None:
        """1タグを正規化する。None は「捨てる」(none 等)。"""
        raw_tag = raw_tag.strip()
        if not raw_tag or is_none(raw_tag):
            return None
        if "{" in raw_tag and "}" in raw_tag:
            return raw_tag  # プレースホルダは素通し
        key = normalize_key(raw_tag)
        if key in LITERAL_TAGS:
            return to_output_form(key)
        if COUNT_RE.match(key) and key not in self.d.canonical:
            # 人数・組の語(duo 等)は Danbooru が廃止していても、Anima の作法として通す。
            return to_output_form(key)
        hit = self.d.lookup(key)
        if hit:
            return self._accept(hit)

        syn = self.synonyms.get(raw_tag.lower())
        if syn:
            hit = self.d.lookup(normalize_key(syn))
            if hit:
                self.replaced.append((raw_tag, to_output_form(hit), "同義語表"))
                return self._accept(hit)

        for v in self._variants(key):
            hit = self.d.lookup(v)
            if hit and self.d.type_of(hit) == "general":
                self.replaced.append((raw_tag, to_output_form(hit), "表記ゆれ"))
                return self._accept(hit)

        # 綴りが近いだけのタグは意味が違うことがある(実測: looking at ball -> looking at bill)。
        # そのため自動では置き換えず、候補としてログに出すだけにする。
        scored = [(r, t) for r, t in self._fuzzy(key)
                  if self.d.count_of(t) >= LOW_COUNT["general"]]
        note = "廃止タグ" if key in self.d.deprecated else ""
        cands = [f"{to_output_form(t)} ({self.d.count_of(t):,})" for _r, t in scored[:3]]
        self.unresolved.append((key + (f" [{note}]" if note else ""), cands))
        return to_output_form(key)

    def normalize_field(self, value: str) -> str:
        """カンマ区切りフィールドを正規化。重複は出現順を保って除去。"""
        seen: set[str] = set()
        out: list[str] = []
        for raw in value.split(","):
            resolved = self.resolve_tag(raw)
            if resolved is None or resolved in seen:
                continue
            seen.add(resolved)
            out.append(resolved)
        return ", ".join(out)

    # ---- BASE(版権キャラ) --------------------------------------------------------
    def resolve_base(self, value: str) -> tuple[str, str | None, list[str]]:
        """BASE を (出力値, キャラタグ or None, 作品タグの一覧) へ。"""
        tokens = [t.strip() for t in value.split(",") if t.strip() and not is_none(t)]
        if not tokens:
            return value, None, []
        first, rest = tokens[0], tokens[1:]
        key = normalize_key(first)
        if ("{" in first) or COUNT_RE.match(key) or key in LITERAL_TAGS:
            return self.normalize_field(value), None, []

        series: list[str] = []
        for r in rest:
            s = self.d.lookup(normalize_key(r))
            if s and self.d.type_of(s) == "copyright":
                series.append(s)

        hit = self.d.lookup(key)
        ch = hit if hit and self.d.type_of(hit) == "character" else None
        if ch is None:
            cands = self.d.character_candidates(key)
            if series:
                matched = [c for c in cands
                           if any(s in self.d.characters.get(c, {}).get("series", [])
                                  or (QUALIFIER_RE.match(c) and QUALIFIER_RE.match(c).group(2) == s)
                                  for s in series)]
                cands = matched or cands
            if cands:
                ch = cands[0]
                alts = ", ".join(to_output_form(c) for c in cands[1:4])
                why = "キャラ名から選択" + (f"(ほかの候補: {alts})" if alts else "")
                self.replaced.append((first, to_output_form(ch), why))
        if ch is None:
            return self.normalize_field(value), None, []

        self._accept(ch)
        if not series:
            series = self.d.characters.get(ch, {}).get("series", [])[:1]
        return to_output_form(ch), ch, series

    def fill_character(self, ch: str, appearance: str, outfit: str) -> tuple[str, str]:
        """版権キャラの既定の特徴で、文章が触れていない部位を補う。"""
        feats = self.d.characters.get(ch)
        if not feats:
            return appearance, outfit
        app = [t for t in (x.strip() for x in appearance.split(",")) if t and not is_none(t)]
        out = [t for t in (x.strip() for x in outfit.split(",")) if t and not is_none(t)]
        added_app: list[str] = []
        if not any(_HAIR_SLOT_RE.search(t) for t in app):
            added_app += [to_output_form(t) for t in feats.get("hair", [])]
        if not any(_EYES_SLOT_RE.search(t) for t in app):
            added_app += [to_output_form(t) for t in feats.get("eyes", [])]
        added_app += [to_output_form(t) for t in feats.get("body", [])
                      if to_output_form(t) not in app]
        added_out: list[str] = []
        if not out:
            added_out = [to_output_form(t) for t in feats.get("outfit", [])]
        name = to_output_form(ch)
        if added_app:
            self.filled.append((name, "APPEARANCE", added_app))
        if added_out:
            self.filled.append((name, "OUTFIT", added_out))
        new_app = ", ".join(dict.fromkeys(added_app + app)) or "none"
        new_out = ", ".join(dict.fromkeys(out + added_out)) or "none"
        return new_app, new_out

    # ---- ログ ------------------------------------------------------------------
    def log_text(self) -> str:
        lines: list[str] = []
        counts = Counter(t for t, _c in self.unresolved)
        cands = {t: c for t, c in self.unresolved}
        lines.append(f"未解決(辞書に無い)タグ: {len(counts)} 種 / 延べ {len(self.unresolved)} 個"
                     "(削除はせず、そのまま出力した)")
        for tag, n in counts.most_common():
            c = cands.get(tag) or []
            lines.append(f"  {tag}\t{n}" + (f"\t候補: {', '.join(c)}" if c else ""))
        if self.replaced:
            lines.append(f"\n自動で置き換えたタグ: {len(self.replaced)} 個")
            for src, dst, why in self.replaced:
                lines.append(f"  {src} -> {dst}\t[{why}]")
        if self.rare:
            uniq = dict(self.rare)
            lines.append(f"\n低頻度のタグ(Anima が覚えていない可能性): {len(uniq)} 種")
            for tag, n in sorted(uniq.items(), key=lambda kv: kv[1]):
                lines.append(f"  {to_output_form(tag)}\t投稿 {n:,}")
        if self.filled:
            lines.append("\nキャラの既定の特徴で補ったタグ:")
            for name, field, tags in self.filled:
                lines.append(f"  {name} {field}: {', '.join(tags)}")
        return "\n".join(lines) + "\n"


def load_map(path: Path | None) -> dict:
    """写像表(interaction_map.json / tag_synonyms.json)を読む。無ければ空。"""
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


# CAPTION 行(自然文+{CHAR_n})は辞書置換しない
_CAPTION_KEYS = {"CAPTION"}
_FIELD_RE = re.compile(r"^(\s*-\s*)([A-Z_0-9]+)\s*:\s*(.*)$")


def process(text: str, norm: Normalizer, imap: dict | None = None) -> tuple[str, list]:
    """構造化ブロックテキストを行単位で処理し、中間データと干渉ノートを返す。"""
    imap = imap or {}
    interaction_notes: list = []
    out_lines: list[str] = []
    current_block = ""
    in_caption_block = False
    # キャラのブロックごとに、BASE・APPEARANCE・OUTFIT の行位置と解決結果を覚える。
    char_blocks: dict[str, dict] = {}

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
        m = _FIELD_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        prefix, key, value = m.group(1), m.group(2), m.group(3)
        if in_caption_block or key in _CAPTION_KEYS:
            out_lines.append(line)  # CAPTION は触らない
            continue
        if current_block.startswith("CHARACTER_") and key == "BASE":
            new_value, ch, series = norm.resolve_base(value)
            info = char_blocks.setdefault(current_block, {})
            info.update({"prefix": prefix, "base_index": len(out_lines),
                         "character": ch, "series": series})
            out_lines.append(f"{prefix}{key}: {new_value}")
            continue
        # 相互干渉の写像表は INTERACTION の COUNT_AND_RELATION だけに効かせる
        if current_block == "INTERACTION" and key == "COUNT_AND_RELATION" and imap:
            value = expand_interaction(value, imap, interaction_notes)
        new_value = norm.normalize_field(value) or "none"
        if current_block.startswith("CHARACTER_") and key in ("APPEARANCE", "OUTFIT"):
            char_blocks.setdefault(current_block, {})[key] = len(out_lines)
        out_lines.append(f"{prefix}{key}: {new_value}")

    # 版権キャラ: 特徴を補い、BASE の直後に SERIES 行を置く(後ろのブロックから挿入する)。
    inserts: list[tuple[int, str]] = []
    for info in char_blocks.values():
        ch = info.get("character")
        if not ch:
            continue
        prefix = info["prefix"]
        ai, oi = info.get("APPEARANCE"), info.get("OUTFIT")
        app = _FIELD_RE.match(out_lines[ai]).group(3) if ai is not None else ""
        out = _FIELD_RE.match(out_lines[oi]).group(3) if oi is not None else ""
        new_app, new_out = norm.fill_character(ch, app, out)
        if ai is not None:
            out_lines[ai] = f"{prefix}APPEARANCE: {new_app}"
        else:
            inserts.append((info["base_index"] + 1, f"{prefix}APPEARANCE: {new_app}"))
        if oi is not None:
            out_lines[oi] = f"{prefix}OUTFIT: {new_out}"
        else:
            inserts.append((info["base_index"] + 1, f"{prefix}OUTFIT: {new_out}"))
        if info["series"]:
            series = ", ".join(to_output_form(s) for s in info["series"])
            inserts.append((info["base_index"] + 1, f"{prefix}SERIES: {series}"))
    for idx, line in sorted(inserts, key=lambda x: -x[0]):
        out_lines.insert(idx, line)

    return "\n".join(out_lines), interaction_notes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dict", type=Path, required=True, help="booru_dict.json")
    p.add_argument("--in", dest="infile", type=Path, default=None,
                   help="抽出器の出力テキスト(省略時は標準入力)")
    p.add_argument("--out", type=Path, default=None,
                   help="中間データの出力先(省略時は標準出力)")
    p.add_argument("--log", type=Path, default=None,
                   help="未解決・置き換え・補完のログ(省略時は標準エラー)")
    p.add_argument("--interaction-map", type=Path, default=HERE / "interaction_map.json",
                   help="相互干渉の写像表(既定: スクリプトと同じ場所の interaction_map.json)")
    p.add_argument("--synonyms", type=Path, default=HERE / "tag_synonyms.json",
                   help="同義語表(既定: スクリプトと同じ場所の tag_synonyms.json)")
    args = p.parse_args(argv)

    if not args.dict.exists():
        print(f"エラー: 辞書 {args.dict} が見つかりません。", file=sys.stderr)
        return 1
    try:
        bdict = BooruDict(json.loads(args.dict.read_text(encoding="utf-8")))
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    imap = load_map(args.interaction_map)
    norm = Normalizer(bdict, load_map(args.synonyms))

    text = args.infile.read_text(encoding="utf-8") if args.infile else sys.stdin.read()
    result, interaction_notes = process(text, norm, imap)

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

    log_text = norm.log_text()
    if args.log:
        args.log.write_text(log_text, encoding="utf-8")
    else:
        sys.stderr.write(log_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
