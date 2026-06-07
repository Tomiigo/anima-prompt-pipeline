#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_anima_dictionary.py
=========================
Anima(circlestone-labs/Anima)用のプロンプト辞書ビルダー。

何をするか
----------
HDiffusion/historical-danbooru-tag-counts と HDiffusion/gelbooru-tags の
CSV(列: tag_string, category_int64, count_int64, alias_string)を入力に取り、

  1) 不要なタグを除外し(カテゴリ/出現数/color+object/任意のブロックリスト)
  2) Danbooru と Gelbooru を統合し(タグ名が衝突したら Gelbooru を優先)
  3) アンダースコア→スペース等の正規化を行い
  4) Gemma が扱いやすい複数フォーマットで出力する。

出力ファイル(--out-dir 配下)
------------------------------
  anima_tags.jsonl        … 1行1タグの完全レコード(辞書の本体)
  alias_to_canonical.json … {別名 → 正規タグ} の対応表(英文→Animaタグ変換の主役)
  vocab.txt               … 正規タグの一覧(出現数の降順)。
  dictionary.csv          … 入力と同じ列構成の、フィルタ済みCSV(任意)

設計上の注意(重要)
--------------------
* count_int64 は "4,220,608" のようにカンマ付き文字列なので、しきい値判定の前に
  数値へ変換している。
* color+object フィルタ(例: red_eyes)は既定では OFF。理由は README 参照。
  サイズ削減には --min-count(出現数しきい値)の方が安全で強力。
* タグをスペース形/アンダースコア形のどちらで出力すべきかは Anima の実例で要確認。
  既定はスペース形。--keep-underscores でアンダースコア形に切替。

使い方の例
----------
  # 最小構成(Gelbooru だけ、出現数1000以上)
  python build_anima_dictionary.py --gelbooru gelbooru.csv --min-count 1000

  # 両方を統合(Gelbooru 優先)、meta も残す、color+object を削る
  python build_anima_dictionary.py \
      --danbooru danbooru.csv --gelbooru gelbooru.csv \
      --min-count 2000 --keep-categories 0,5 --drop-color-object \
      --out-dir ./anima_dict

  # 推奨:一般+アーティストを含め、低い下限で残しつつカテゴリ別に調整
  #   一般(0)は10未満を除外、アーティスト(1)は50未満を除外
  python build_anima_dictionary.py \
      --danbooru danbooru.csv --gelbooru gelbooru.csv \
      --keep-categories 0,1 --min-count 10 --min-count-by-category "1:50" \
      --out-dir ./anima_dict
"""

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd


# =====================================================================
# カテゴリ定義(ユーザー提供のマッピング)
# =====================================================================
CATEGORY_NAMES = {
    0: "general",    # 説明的タグ・身体的特徴・物・動作
    1: "artist",     # アーティスト名
    2: "unused",     # 未使用
    3: "copyright",  # シリーズ/作品名
    4: "character",  # キャラ名
    5: "meta",       # 媒体・解像度・コメント等
}


# =====================================================================
# color+object フィルタ用の語彙(必要に応じて編集してください)
#   tag を "_" で分割し、末尾が NOUNS に含まれ、かつ先頭側のトークンが
#   すべて COLORS の場合に「色+対象」とみなして除外する。
#   例: light_blue_eyes -> [light, blue, eyes] -> 除外
#       very_long_hair  -> [very, long, hair]  -> 残す(very/long は色でない)
# =====================================================================
COLORS = {
    "red", "orange", "yellow", "green", "blue", "purple", "violet", "pink",
    "brown", "black", "white", "grey", "gray", "silver", "gold", "blonde",
    "blond", "cyan", "magenta", "tan", "beige", "cream", "navy", "teal",
    "lavender", "maroon", "crimson", "scarlet", "azure", "turquoise",
    "emerald", "amber", "light", "dark", "pale", "deep", "bright",
    "two-tone", "multicolored", "rainbow",
}
# フィルタONでも残したい「非自明な正規色名」。Anima がこの綴りで学習している。
PROTECTED_COLOR_TERMS = {"aqua", "aquamarine"}
NOUNS = {
    "eye", "eyes", "hair", "pupils", "sclera", "eyelashes", "eyeshadow",
    "eyebrows", "lips", "lipstick", "nails", "fingernails", "toenails",
    "skin",  # ※ dark_skin / pale_skin も消えます。残したいなら skin を外す
    "dress", "shirt", "t-shirt", "skirt", "jacket", "coat", "pants",
    "shorts", "trousers", "gloves", "thighhighs", "pantyhose", "socks",
    "kneehighs", "bikini", "swimsuit", "kimono", "sweater", "hoodie",
    "cardigan", "vest", "cape", "cloak", "scarf", "muffler", "ribbon",
    "bow", "bowtie", "necktie", "tie", "hairband", "headband", "hairclip",
    "hat", "cap", "beret", "helmet", "shoes", "boots", "sandals", "heels",
    "footwear", "leotard", "bodysuit", "apron", "collar", "choker", "bra",
    "panties", "underwear", "wings", "horns", "tail", "fur", "feathers",
}


# =====================================================================
# meta(category 5)のうち、画像生成に無関係な「投稿管理タグ」の例。
#   --keep-categories に 5 を含めても、これらは既定で除外する。
#   highres / absurdres / lowres / monochrome などの品質・媒体タグは
#   生成に使えるので、ここには入れていない。
#   無効化したい場合は --keep-meta-junk を指定。
# =====================================================================
META_JUNK = {
    "tagme", "commentary", "commentary_request", "commentary_typo",
    "partial_commentary", "translated", "translation_request",
    "check_translation", "hard_translated", "poorly_translated",
    "bad_id", "bad_pixiv_id", "bad_twitter_id", "bad_link", "bad_source",
    "md5_mismatch", "duplicate", "image_sample", "artist_request",
    "source_request", "character_request", "copyright_request",
    "third-party_edit", "revision", "variant_set", "has_bad_revision",
    "has_downscaled_revision", "paid_reward_available", "tagme_(artist)",
}


# =====================================================================
# ヘルパー
# =====================================================================
# 別名セル中のプレースホルダ。これらは別名として採用しない(大文字小文字無視)。
NULL_TOKENS = {"", "null", "nan", "none", "n/a", "na", "<na>"}


def parse_count(series: pd.Series) -> pd.Series:
    """count_int64('4,964,838' 等の文字列)を整数へ変換する。"""
    s = series.astype("string").str.replace(r"[^\d]", "", regex=True)
    return pd.to_numeric(s, errors="coerce").fillna(0).astype("int64")


def split_aliases(raw) -> list:
    """alias_string をカンマ分割し、空白除去・プレースホルダ('null'等)除去して返す。"""
    if raw is None or (isinstance(raw, float)):
        return []
    out = []
    for piece in str(raw).split(","):
        piece = piece.strip()
        if piece and piece.lower() not in NULL_TOKENS:
            out.append(piece)
    return out


def normalize_tag(tag: str, *, to_space: bool, escape_parens: bool) -> str:
    """正規化: 前後空白除去 / アンダースコア→スペース(任意) / 括弧エスケープ(任意)。"""
    if tag is None:
        return ""
    t = str(tag).strip()
    if to_space:
        t = t.replace("_", " ")
    if escape_parens:
        # SD系プロンプトでは ( ) は強調記号なので \( \) にエスケープ
        t = re.sub(r"(?<!\\)\(", r"\\(", t)
        t = re.sub(r"(?<!\\)\)", r"\\)", t)
    return t


def is_color_object(tag_underscore: str) -> bool:
    """tag が『色+対象』(例: red_eyes, light_blue_hair)かどうか。"""
    tokens = tag_underscore.split("_")
    if len(tokens) < 2:
        return False
    noun = tokens[-1]
    prefix = tokens[:-1]
    if noun not in NOUNS:
        return False
    if not all(tok in COLORS for tok in prefix):
        return False
    if set(prefix) & PROTECTED_COLOR_TERMS:  # 保護色は残す
        return False
    return True


def load_blocklist(path: Path):
    """ブロックリストを読み込む。'#' 始まりは無視。're:' 始まりは正規表現。"""
    exact, patterns = set(), []
    if path is None:
        return exact, patterns
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            patterns.append(re.compile(line[3:]))
        else:
            exact.add(line)
    return exact, patterns


# 入力CSVで想定する列(この順序)。ヘッダー無しCSVのときに先頭から割り当てる。
_CANONICAL_COLS = ["tag_string", "category_int64", "count_int64", "alias_string"]


def _looks_like_header(path: Path) -> bool:
    """CSV の 1 行目がヘッダー行かを判定する。HDiffusion(John Steward)配布の
    生CSV はヘッダーが無いため、その場合に後段で列名を割り当てられるようにする。
    判定は主に『2 列目(カテゴリの位置)が整数ならデータ行』という構造で行う。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return True  # 読めないときは従来どおりヘッダー有り扱い(後段で別途エラー)
    if not first.strip():
        return False
    cells = [c.strip().strip('"').strip("'").lower() for c in first.split(",")]
    # 1 列目が既知のタグ列名ならヘッダー
    if cells and cells[0] in ("tag_string", "tag", "name"):
        return True
    # 2 列目(カテゴリの位置)が整数 -> データ行、非整数 -> ヘッダー行
    if len(cells) >= 2:
        return re.fullmatch(r"-?\d+", cells[1].replace(",", "")) is None
    return False


def load_csv(path: Path, source: str) -> pd.DataFrame:
    """入力CSVを読み込み、列名を揃え、count を整数化、source を付与する。
    ヘッダーの有無は自動判定する(ヘッダー無しなら既知の列順で名前を与える)。"""
    if _looks_like_header(path):
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    else:
        # ヘッダー無し: 既知の列順 _CANONICAL_COLS を先頭から割り当てる
        df = pd.read_csv(path, header=None, dtype=str,
                         keep_default_na=False, na_values=[""])
        ncols = df.shape[1]
        if ncols < 3:
            sys.exit(f"[エラー] {path}: 列数 {ncols} はヘッダー無しCSVとして不足です"
                     f"(想定列順: {_CANONICAL_COLS})。")
        names = _CANONICAL_COLS[:ncols] + [
            f"extra_{i}" for i in range(max(0, ncols - len(_CANONICAL_COLS)))
        ]
        df.columns = names
        print(f"[情報] {path.name}: ヘッダー無しと判定し、列名 "
              f"{_CANONICAL_COLS[:ncols]} を割り当てました。")

    # 列名のゆらぎを吸収(tag_string / tag、count_int64 / count など)
    rename = {}
    for col in df.columns:
        low = col.lower()
        if low in ("tag_string", "tag", "name"):
            rename[col] = "tag_string"
        elif "categor" in low:
            rename[col] = "category_int64"
        elif "count" in low or low in ("post_count", "posts"):
            rename[col] = "count_int64"
        elif "alias" in low:
            rename[col] = "alias_string"
    df = df.rename(columns=rename)

    for required in ("tag_string", "category_int64", "count_int64"):
        if required not in df.columns:
            sys.exit(f"[エラー] {path}: 必須列 '{required}' が見つかりません。"
                     f" 検出した列: {list(df.columns)}")
    if "alias_string" not in df.columns:
        df["alias_string"] = pd.NA

    df["category_int64"] = pd.to_numeric(df["category_int64"], errors="coerce").fillna(-1).astype("int64")
    df["count_int64"] = parse_count(df["count_int64"])
    df["tag_string"] = df["tag_string"].astype("string").str.strip()
    df = df[df["tag_string"].notna() & (df["tag_string"] != "")]
    df["source"] = source
    return df[["tag_string", "category_int64", "count_int64", "alias_string", "source"]]


# =====================================================================
# フィルタ
# =====================================================================
def parse_min_count_by_category(spec: str) -> dict:
    """ "1:50,4:100" のような指定を {1: 50, 4: 100} に解析する。"""
    out: dict = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition(":")
        try:
            out[int(k)] = int(v)
        except ValueError:
            raise SystemExit(f"--min-count-by-category の指定が不正です: {part!r}")
    return out


def apply_filters(df: pd.DataFrame, args, report: dict) -> pd.DataFrame:
    n0 = len(df)
    keep_cats = {int(x) for x in args.keep_categories.split(",") if x.strip() != ""}

    # 1) カテゴリ(既定で artist=1 / copyright=3 / character=4 を除外)
    df = df[df["category_int64"].isin(keep_cats)]
    report["after_category"] = len(df)

    # 2) meta のゴミタグ
    if not args.keep_meta_junk:
        mask_junk = (df["category_int64"] == 5) & (df["tag_string"].isin(META_JUNK))
        df = df[~mask_junk]
    report["after_meta_junk"] = len(df)

    # 3) 出現数しきい値(最も効くサイズ削減)。カテゴリ別の上書きに対応。
    per_cat = parse_min_count_by_category(getattr(args, "min_count_by_category", ""))
    if per_cat:
        thr = df["category_int64"].map(lambda c: per_cat.get(int(c), args.min_count))
        df = df[df["count_int64"] >= thr]
    else:
        df = df[df["count_int64"] >= args.min_count]
    report["after_min_count"] = len(df)

    # 4) color+object(既定 OFF)
    if args.drop_color_object:
        mask_co = df["tag_string"].map(is_color_object)
        df = df[~mask_co]
    report["after_color_object"] = len(df)

    # 5) 任意のブロックリスト
    exact, patterns = load_blocklist(args.blocklist)
    if exact or patterns:
        def blocked(tag: str) -> bool:
            if tag in exact:
                return True
            return any(p.search(tag) for p in patterns)
        df = df[~df["tag_string"].map(blocked)]
    report["after_blocklist"] = len(df)

    report["input_rows"] = n0
    return df


# =====================================================================
# 統合(Gelbooru 優先)
# =====================================================================
def merge_sources(dfs, report: dict) -> pd.DataFrame:
    """tag_string が衝突したら Gelbooru を採用し、別名は両者の和を取る。"""
    combined = pd.concat(dfs, ignore_index=True)

    # source の優先度: gelbooru を上に来させてから tag で集約
    priority = {"gelbooru": 0, "danbooru": 1}
    combined["__pri"] = combined["source"].map(priority).fillna(2)

    def merge_group(g: pd.DataFrame) -> pd.Series:
        g = g.sort_values("__pri")
        winner = g.iloc[0]                      # 優先 source の行(綴りを採用)
        sources = sorted(set(g["source"]))
        # カテゴリ: いずれかのソースが character(4) / copyright(3) / artist(1) と
        # 判定したタグは、別ソースが general(0) でも、その「素性」を優先する。
        # これにより「片方の出典で一般とラベルされたキャラ/版権/アーティスト名」が
        # 一般タグとして候補に紛れ込むのを防ぐ(Gelbooru 優先より素性判定を優先)。
        # artist 辞書(--keep-categories 1)を作るときは 1 が残るので両立する。
        cats = {int(c) for c in g["category_int64"]}
        if 4 in cats:
            category = 4
        elif 3 in cats:
            category = 3
        elif 1 in cats:
            category = 1
        else:
            category = int(winner["category_int64"])
        # 別名は全 source の和集合
        aliases = set()
        for a in g["alias_string"].dropna():
            aliases.update(split_aliases(a))
        return pd.Series({
            "category_int64": category,
            "count_int64": int(g["count_int64"].max()),
            "alias_string": ",".join(sorted(aliases)) if aliases else pd.NA,
            "source": "both" if len(sources) > 1 else sources[0],
        })

    merged = combined.groupby("tag_string", sort=False).apply(
        merge_group, include_groups=False
    ).reset_index()

    report["merged_unique_tags"] = len(merged)
    report["collisions_gelbooru_won"] = int(
        (combined.duplicated("tag_string", keep=False)).sum()
    )
    return merged.drop(columns=[c for c in ("__pri",) if c in merged.columns])


# =====================================================================
# 出力生成
# =====================================================================
def build_outputs(df: pd.DataFrame, args, report: dict):
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    to_space = not args.keep_underscores

    df = df.sort_values("count_int64", ascending=False).reset_index(drop=True)

    records = []
    alias_map = {}            # 別名(正規化後) -> 正規タグ(正規化後)
    alias_conflicts = 0

    for _, row in df.iterrows():
        canon_us = row["tag_string"]
        canon = normalize_tag(canon_us, to_space=to_space, escape_parens=args.escape_parens)
        cat_name = CATEGORY_NAMES.get(int(row["category_int64"]), "unknown")

        # 別名の正規化(プレースホルダ・重複・正規タグと同一・空を除去)
        aliases_out = []
        if not args.no_aliases:
            seen = set()
            for piece in split_aliases(row["alias_string"]):
                a = normalize_tag(piece, to_space=to_space, escape_parens=args.escape_parens)
                if not a or a == canon or a in seen:
                    continue
                seen.add(a)
                aliases_out.append(a)

        records.append({
            "tag": canon,
            "tag_underscore": canon_us,
            "category": cat_name,
            "count": int(row["count_int64"]),
            "source": row["source"],
            "aliases": aliases_out,
        })

        # alias_map: 正規タグ自身 + 各別名 を正規タグに向ける
        #   df は出現数降順なので、衝突時は出現数の多い正規タグが勝つ(setdefault)
        if canon not in alias_map:
            alias_map[canon] = canon
        for a in aliases_out:
            if a in alias_map and alias_map[a] != canon:
                alias_conflicts += 1
                continue
            alias_map.setdefault(a, canon)

    # ---- 1) JSONL(本体)----
    jsonl_path = out_dir / "anima_tags.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- 2) alias_to_canonical.json ----
    alias_path = out_dir / "alias_to_canonical.json"
    alias_path.write_text(
        json.dumps(dict(sorted(alias_map.items())), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ---- 3) vocab.txt(出現数降順)----
    vocab_path = out_dir / "vocab.txt"
    vocab_path.write_text(
        "\n".join(rec["tag"] for rec in records) + "\n", encoding="utf-8"
    )

    # ---- 4) dictionary.csv(任意・入力と同じ列構成)----
    csv_path = None
    if args.write_csv:
        csv_path = out_dir / "dictionary.csv"
        out_df = df.copy()
        out_df["tag_string"] = out_df["tag_string"].map(
            lambda t: normalize_tag(t, to_space=to_space, escape_parens=args.escape_parens)
        )
        out_df.rename(columns={"category_int64": "category", "count_int64": "count"}, inplace=True)
        out_df[["tag_string", "category", "count", "alias_string", "source"]].to_csv(
            csv_path, index=False
        )

    report["final_tags"] = len(records)
    report["alias_map_entries"] = len(alias_map)
    report["alias_conflicts_skipped"] = alias_conflicts
    report["bytes_jsonl"] = jsonl_path.stat().st_size
    report["bytes_alias_map"] = alias_path.stat().st_size
    report["bytes_vocab"] = vocab_path.stat().st_size
    report["paths"] = {
        "jsonl": str(jsonl_path),
        "alias_map": str(alias_path),
        "vocab": str(vocab_path),
        "csv": str(csv_path) if csv_path else None,
    }


# =====================================================================
# ビルド結果の表示(サマリ)
# =====================================================================
def print_report(report: dict, df: pd.DataFrame):
    def kb(n): return f"{n/1024:,.0f} KB" if n < 1024**2 else f"{n/1024**2:,.1f} MB"

    print("\n" + "=" * 60)
    print(" Anima 辞書ビルド結果")
    print("=" * 60)
    print(f"  入力行数(合計)            : {report.get('input_rows', 0):>12,}")
    print(f"  カテゴリ絞り込み後          : {report.get('after_category', 0):>12,}")
    print(f"  meta ゴミ除去後             : {report.get('after_meta_junk', 0):>12,}")
    print(f"  出現数しきい値後            : {report.get('after_min_count', 0):>12,}")
    print(f"  color+object 除去後         : {report.get('after_color_object', 0):>12,}")
    print(f"  ブロックリスト後            : {report.get('after_blocklist', 0):>12,}")
    if "merged_unique_tags" in report:
        print(f"  統合後のユニークタグ        : {report['merged_unique_tags']:>12,}")
        print(f"  名前衝突(Gelbooru優先)行  : {report.get('collisions_gelbooru_won', 0):>12,}")
    print("-" * 60)
    print(f"  最終タグ数                  : {report.get('final_tags', 0):>12,}")
    print(f"  alias_map のエントリ数      : {report.get('alias_map_entries', 0):>12,}")
    print(f"  別名衝突でスキップ          : {report.get('alias_conflicts_skipped', 0):>12,}")
    print("-" * 60)
    if not df.empty:
        cats = df["category_int64"].map(CATEGORY_NAMES).value_counts()
        print("  カテゴリ内訳:")
        for name, n in cats.items():
            print(f"     {name:<12}: {n:>10,}")
    print("-" * 60)
    print(f"  anima_tags.jsonl       : {kb(report.get('bytes_jsonl', 0))}")
    print(f"  alias_to_canonical.json: {kb(report.get('bytes_alias_map', 0))}")
    print(f"  vocab.txt              : {kb(report.get('bytes_vocab', 0))}")
    print("=" * 60)
    print("  出力先:")
    for k, v in report.get("paths", {}).items():
        if v:
            print(f"     {k:<10}: {v}")
    print()


# =====================================================================
# メイン
# =====================================================================
def main():
    p = argparse.ArgumentParser(
        description="Anima 用プロンプト辞書ビルダー",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--danbooru", type=Path, help="Danbooru タグCSVのパス")
    p.add_argument("--gelbooru", type=Path, help="Gelbooru タグCSVのパス(衝突時こちらを優先)")
    p.add_argument("--min-count", type=int, default=1000,
                   help="この出現数未満のタグを除外(カテゴリ別指定がない場合の既定)")
    p.add_argument("--min-count-by-category", type=str, default="",
                   help='カテゴリ別に出現数しきい値を上書き。"カテゴリ番号:しきい値" の'
                        'カンマ区切り(例: "1:50,4:100")。0=general 1=artist '
                        '3=copyright 4=character 5=meta。未指定カテゴリは --min-count を使用')
    p.add_argument("--keep-categories", type=str, default="0",
                   help="残すカテゴリ。0=general 5=meta。例: '0,5'。"
                        "既定の '0' で artist/copyright/character/meta を除外")
    p.add_argument("--drop-color-object", action="store_true",
                   help="red_eyes 等の『色+対象』タグを除外(既定OFF)")
    p.add_argument("--keep-meta-junk", action="store_true",
                   help="meta のゴミタグ(tagme等)も残す")
    p.add_argument("--blocklist", type=Path,
                   help="除外したいタグの一覧ファイル。're:'始まりは正規表現")
    p.add_argument("--no-aliases", action="store_true",
                   help="別名を一切含めない(サイズ最小化)")
    p.add_argument("--keep-underscores", action="store_true",
                   help="タグをアンダースコア形のまま出力(既定はスペース形)")
    p.add_argument("--no-escape-parens", dest="escape_parens",
                   action="store_false", help="括弧を \\( \\) にエスケープしない")
    p.add_argument("--write-csv", action="store_true",
                   help="dictionary.csv も出力する")
    p.add_argument("--out-dir", type=Path, default=Path("./anima_dict"),
                   help="出力ディレクトリ")
    p.set_defaults(escape_parens=True)
    args = p.parse_args()

    if not args.danbooru and not args.gelbooru:
        p.error("--danbooru か --gelbooru の少なくとも一方を指定してください。")

    report = {}
    dfs = []
    if args.gelbooru:
        print(f"[読込] Gelbooru: {args.gelbooru}")
        dfs.append(load_csv(args.gelbooru, "gelbooru"))
    if args.danbooru:
        print(f"[読込] Danbooru: {args.danbooru}")
        dfs.append(load_csv(args.danbooru, "danbooru"))

    if len(dfs) == 1:
        merged = dfs[0].copy()
        merged = merged[["tag_string", "category_int64", "count_int64", "alias_string", "source"]]
        report["input_rows_premerge"] = len(merged)
    else:
        merged = merge_sources(dfs, report)

    filtered = apply_filters(merged, args, report)
    build_outputs(filtered, args, report)
    print_report(report, filtered)


if __name__ == "__main__":
    main()
