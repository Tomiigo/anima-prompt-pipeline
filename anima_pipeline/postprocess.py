"""選択肢1 - 決定論的な後処理。

build_anima_dictionary.py が生成した alias->canonical マップを使い、自由な
英語テキスト(またはモデルのタグ下書き)を Anima の正規タグへスナップする。

2 つの役割:
  * canonicalize(text)  - 任意の英語を走査して正規タグを取り出す
                          (完全一致・複数語・最長一致優先; O(n+z))。
  * snap_tag(tag)       - 1 つのタグを検証/正規化する(完全一致、次にファジー)。
                          モデル出力に対する最終的な安全ゲートとして使う。

Aho-Corasick の検索は O(n + z) で辞書サイズに依存しないため、この層は数万個の
タグでも実行時コストなしにスケールする。エイリアスリストにない言い換えは扱えず、
文脈も理解しない —— それは上位の Gemma 生成側の役割である。
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Iterable

try:
    import ahocorasick
except ImportError as e:  # pragma: no cover
    raise SystemExit("Missing dependency: pip install pyahocorasick") from e


def _boundary_ok(text: str, start: int, end: int) -> bool:
    """[start, end] の範囲(end を含む)が単語境界に収まっていれば True を返す。
    これにより 'red' が 'tired' の内側にマッチしない。アンダースコアは booru
    タグで使われるため、単語構成文字として扱う。"""
    def is_word(c: str) -> bool:
        return c.isalnum() or c == "_"
    left = start == 0 or not is_word(text[start - 1])
    right = end == len(text) - 1 or not is_word(text[end + 1])
    return left and right


class Normalizer:
    def __init__(self, alias_map: dict[str, str], use_fuzzy: bool = True,
                 fuzzy_cutoff: int = 88):
        # 表層フレーズ(小文字化)-> 正規タグ
        self.alias_map: dict[str, str] = {k.lower(): v for k, v in alias_map.items()}
        # すべての正規タグが自分自身を指すようにし、正規の綴りもマッチさせる
        for canon in set(alias_map.values()):
            self.alias_map.setdefault(canon.lower(), canon)

        self.automaton = ahocorasick.Automaton()
        for phrase, canon in self.alias_map.items():
            if phrase:
                self.automaton.add_word(phrase, (len(phrase), canon))
        self.automaton.make_automaton()

        self.use_fuzzy = use_fuzzy
        self.fuzzy_cutoff = fuzzy_cutoff
        self._fuzzy_choices = list(self.alias_map.keys()) if use_fuzzy else []

    @classmethod
    def from_file(cls, path: str | Path, **kw) -> "Normalizer":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f), **kw)

    # -- テキスト全体の走査 ---------------------------------------------------
    def canonicalize(self, text: str) -> tuple[list[str], list[tuple[int, int, str]]]:
        """(順序付きの一意な正規タグ, マッチした範囲) を返す。

        重なりは貪欲に解決し、長いマッチを優先する。返されるタグは、テキスト中で
        最初に出現した順に並ぶ。
        """
        t = text.lower()
        raw = []  # (開始, 終端index(含む), 長さ, 正規タグ)
        for end_idx, (length, canon) in self.automaton.iter(t):
            start = end_idx - length + 1
            if _boundary_ok(t, start, end_idx):
                raw.append((start, end_idx, length, canon))

        raw.sort(key=lambda r: (-r[2], r[0]))     # 長いもの優先、次に左端優先
        occupied = [False] * len(t)
        spans: list[tuple[int, int, str]] = []
        for start, end, _length, canon in raw:
            if any(occupied[start:end + 1]):
                continue
            for i in range(start, end + 1):
                occupied[i] = True
            spans.append((start, end, canon))

        spans.sort(key=lambda s: s[0])            # テキスト中の位置順
        seen: set[str] = set()
        tags: list[str] = []
        for _s, _e, canon in spans:
            if canon not in seen:
                seen.add(canon)
                tags.append(canon)
        return tags, spans

    # -- 単一タグの検証 -------------------------------------------------------
    def snap_tag(self, tag: str) -> str | None:
        """1 つのタグを正規化する。辞書に完全一致すればそれを、なければファジー
        一致を、それもなければ None(既知のタグではない、の意)を返す。"""
        key = tag.strip().lower()
        if not key:
            return None
        if key in self.alias_map:
            return self.alias_map[key]
        if self.use_fuzzy and self._fuzzy_choices:
            from rapidfuzz import process, fuzz
            hit = process.extractOne(
                key, self._fuzzy_choices, scorer=fuzz.ratio,
                score_cutoff=self.fuzzy_cutoff,
            )
            if hit:
                return self.alias_map[hit[0]]
        return None

    def snap_tags(self, tags: Iterable[str],
                  keep_unknown: bool = False) -> tuple[list[str], list[str]]:
        """タグのリストを正規化する。(kept, dropped) を返す。未知のタグは
        keep_unknown=True でない限り捨てられる(True なら原文のまま残す)。"""
        kept: list[str] = []
        dropped: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            canon = self.snap_tag(tag)
            if canon is None:
                if keep_unknown and tag.strip() and tag not in seen:
                    seen.add(tag)
                    kept.append(tag.strip())
                else:
                    dropped.append(tag)
            elif canon not in seen:
                seen.add(canon)
                kept.append(canon)
        return kept, dropped
