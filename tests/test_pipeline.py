#!/usr/bin/env python3
"""②正規化と③組み立ての回帰テスト(標準ライブラリのみ。辞書は小さな模型を使う)。

  python3 -m unittest discover tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import assemble  # noqa: E402
import normalize  # noqa: E402

TOY_DICT = {
    "format": 2,
    "canonical": {
        "1girl": ["general", 100000], "1boy": ["general", 100000], "2girls": ["general", 100000],
        "blonde_hair": ["general", 100000], "long_hair": ["general", 100000],
        "white_hair": ["general", 100000], "purple_hair": ["general", 100000],
        "green_eyes": ["general", 100000], "purple_eyes": ["general", 100000],
        "pointy_ears": ["general", 100000], "elf": ["general", 100000],
        "capelet": ["general", 100000], "dress": ["general", 100000],
        "blue_socks": ["general", 10000], "socks": ["general", 100000],
        "tan": ["general", 100000], "muscular": ["general", 100000],
        "from_above": ["general", 100000], "ponytail": ["general", 100000],
        "fern": ["general", 1000],
        "frieren": ["character", 11000],
        "fern_(sousou_no_frieren)": ["character", 7000],
        "fern_(adventure_time)": ["character", 300],
        "sousou_no_frieren": ["copyright", 20000],
        "male_focus": ["general", 100000],
    },
    "aliases": {"tanned_skin": "tan", "male": "male_focus"},
    "deprecated": ["blue_legwear"],
    "characters": {
        "frieren": {"series": ["sousou_no_frieren"], "hair": ["long_hair", "white_hair"],
                    "eyes": ["green_eyes"], "body": ["elf", "pointy_ears"],
                    "outfit": ["capelet"]},
        "fern_(sousou_no_frieren)": {"series": ["sousou_no_frieren"],
                                     "hair": ["purple_hair"], "eyes": ["purple_eyes"],
                                     "outfit": ["dress"]},
    },
}

SYNONYMS = {"high angle": "from above"}


def run(extracted: str) -> tuple[str, normalize.Normalizer]:
    norm = normalize.Normalizer(normalize.BooruDict(TOY_DICT), SYNONYMS)
    normalized, _notes = normalize.process(extracted, norm, {})
    return assemble.build(normalized), norm


class AnchorTest(unittest.TestCase):
    def test_no_duplicated_gender_word(self):
        # 旧版は "young adult male male" を出していた。
        out, _ = run("[INTERACTION]\n- COUNT_AND_RELATION: 1boy\n[CHARACTER_1]\n- BASE: 1boy\n"
                     "- APPEARANCE: young adult male, tanned skin, muscular\n"
                     "- EXPRESSION: none visible\n- OUTFIT: none visible\n- POSE: standing\n"
                     "[NATURAL_LANGUAGE]\n- CAPTION: {CHAR_1} is on the right.\n")
        self.assertNotIn("male male", out)
        self.assertNotIn("young adult male", out)
        self.assertNotIn("none visible", out)
        self.assertNotIn("male focus", out)
        self.assertIn("tan muscular male is on the right", out)


class NormalizeTest(unittest.TestCase):
    def test_canonical_tag_is_not_aliased_away(self):
        norm = normalize.Normalizer(normalize.BooruDict(TOY_DICT))
        self.assertEqual(norm.resolve_tag("blue socks"), "blue socks")

    def test_synonym_and_variant_replacement(self):
        norm = normalize.Normalizer(normalize.BooruDict(TOY_DICT), SYNONYMS)
        self.assertEqual(norm.resolve_tag("high angle"), "from above")
        self.assertEqual(norm.resolve_tag("pony tail"), "ponytail")

    def test_unknown_tag_is_kept_and_logged(self):
        norm = normalize.Normalizer(normalize.BooruDict(TOY_DICT))
        self.assertEqual(norm.resolve_tag("blue legwear"), "blue legwear")
        self.assertIn("blue_legwear", norm.log_text())

    def test_old_dictionary_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize.BooruDict({"canonical": {}, "aliases": {}})


class CharacterTest(unittest.TestCase):
    EXTRACTED = ("[INTERACTION]\n- COUNT_AND_RELATION: 2girls\n"
                 "[CHARACTER_1]\n- BASE: frieren, sousou no frieren\n- APPEARANCE: none\n"
                 "- EXPRESSION: smile\n- OUTFIT: none\n- POSE: sitting\n"
                 "[CHARACTER_2]\n- BASE: fern\n- APPEARANCE: blonde hair\n"
                 "- EXPRESSION: none\n- OUTFIT: serafuku\n- POSE: standing\n")

    def test_character_resolution_fill_and_order(self):
        out, norm = run(self.EXTRACTED)
        positive = out.splitlines()[0]
        # 公式カードの順: 人数 → キャラ → 作品
        self.assertIn("2girls, frieren, fern \\(sousou no frieren\\), sousou no frieren", positive)
        # 文章が触れていない部位は補う(服も)
        self.assertIn("white hair", positive)
        self.assertIn("capelet", positive)
        # 文章が指定した髪・服は上書きしない
        self.assertNotIn("purple hair", positive)
        self.assertNotIn("dress", positive)
        # 名前だけのときは投稿数で選び、ほかの候補をログに残す
        self.assertIn("fern (adventure time)", norm.log_text())

    def test_parentheses_are_escaped_once(self):
        self.assertEqual(assemble.escape_parens("a (b) \\(c\\)"), "a \\(b\\) \\(c\\)")


if __name__ == "__main__":
    unittest.main()
