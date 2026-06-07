"""日本語 -> Anima プロンプトパイプラインの中央設定ファイル。

このファイルを編集して、データの場所・llama-server のポート・挙動を調整します。
パスはこのファイルからの相対で解決されるため、どのディレクトリから実行しても
パイプラインは動作します。

処理の流れ:
    日本語 -> (Gemma) 英語 -> (Gemma + anima_rules.txt) 順序どおりの Anima タグ列
    -> 辞書でスナップ補正(別名->正規形・近い実在タグへ寄せる) -> ネガティブ付与 -> 完成
起動して使うサーバーは Gemma(CHAT_URL)だけです。
"""
from __future__ import annotations
from pathlib import Path

# --------------------------------------------------------------------------
# パス
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"            # <-- danbooru.csv / gelbooru.csv をここに置く
DICT = DATA / "dict"          # <-- build_anima_dictionary.py がここに書き出す
PROMPTS = ROOT / "prompts"

# 辞書ファイル(build_anima_dictionary.py が生成)。スナップ補正に使う。
ALIAS_MAP = DICT / "alias_to_canonical.json"   # {表層フレーズ -> 正規タグ}
TAGS_JSONL = DICT / "anima_tags.jsonl"         # 正規タグ 1 件につき 1 レコード

# アーティスト辞書(任意・別ビルド: --keep-categories 1 で data/dict_artist へ)。
# プロンプト本文中のアーティスト名を完全一致で拾って @ 付きで注入するために使う。
ARTIST_DICT = DATA / "dict_artist"
ARTIST_ALIAS_MAP = ARTIST_DICT / "alias_to_canonical.json"
ARTIST_TAGS_JSONL = ARTIST_DICT / "anima_tags.jsonl"

# キャラ / 作品(シリーズ)辞書(任意・別ビルド: --keep-categories 3,4 で data/dict_char へ)。
# プロンプト本文中のキャラ名・作品名を完全一致で拾い、キャラブロックの頭
# (@アーティストの前)へ注入するために使う。
CHAR_DICT = DATA / "dict_char"
CHAR_ALIAS_MAP = CHAR_DICT / "alias_to_canonical.json"
CHAR_TAGS_JSONL = CHAR_DICT / "anima_tags.jsonl"

# コミュニティで調整するプロンプトファイル。
RULES_PROMPT = PROMPTS / "anima_rules.txt"     # 共有して人間が編集するルール
FEWSHOT = PROMPTS / "fewshot.txt"              # few-shot のお手本(任意。空でもよい)

# --------------------------------------------------------------------------
# llama-server のエンドポイント(別プロセスとして起動。README.md を参照)
# 使うのは Gemma の 1 本だけ。
# --------------------------------------------------------------------------
CHAT_URL = "http://127.0.0.1:8080"     # Gemma 4(チャット / 翻訳 / 生成)

# --------------------------------------------------------------------------
# 挙動
# --------------------------------------------------------------------------
TRANSLATE_FIRST = True           # 生成の前に Gemma で日本語 -> 英語に翻訳する。
                                 # False の場合は日本語のまま Gemma に渡す。

# prompts/fewshot.txt のお手本を Gemma に提示する。False でゼロショット(お手本なし)。
# ファイルが空でも自動的にゼロショットになる。
USE_FEWSHOT = True

# プロンプト本文に書かれたアーティスト名を artist 辞書(ARTIST_*)で完全一致検出し、
# @ 付きで注入する。ファイルが無ければ自動的に無効。
USE_ARTIST_DICT = True
# この文字数未満のアーティスト名マッチは誤検出(短い一般語との衝突)を避けるため無視する。
ARTIST_MIN_SURFACE_LEN = 3

# プロンプト本文に書かれたキャラ名・作品名を char 辞書(CHAR_*)で完全一致検出し、
# キャラブロックの頭へ注入する。ファイルが無ければ自動的に無効。
# 単一被写体の単純なケース向け。複数キャラ作品の混在は今のところ未対応。
USE_CHAR_DICT = True
# この文字数未満のキャラ/作品名マッチは誤検出(短い一般語との衝突)を避けるため無視する。
CHAR_MIN_SURFACE_LEN = 3

GEN_TEMPERATURE = 0.4            # 生成の temperature(ルール遵守向け)
GEN_MAX_TOKENS = 2048           # 出力の最大トークン。思考(reasoning)を切っても余裕を持たせる。

# --------------------------------------------------------------------------
# スナップ補正(後段で完璧なリストにタグを寄せる)
# --------------------------------------------------------------------------
# カンマ区切りの 1 要素が、この語数以下なら「タグらしい語」とみなしてスナップ照合する。
# これより長いものは説明的な句(例: "white flowy maxi dress with ...")とみなし、
# 単一タグではないのでそのまま残す。
SNAP_MAX_WORDS = 4
# ファジー一致の閾値(rapidfuzz の ratio、0-100)。高いほど誤った寄せを防ぐ。
# 完全一致が無いタグだけがこのファジー経路に入る。
SNAP_FUZZY_CUTOFF = 90

# --------------------------------------------------------------------------
# ネガティブプロンプト(Anima 公式ガイドの実用テンプレート)
# 状況依存の追記(指の本数・俯瞰の上下制御など)は今後のステップで足す。
# --------------------------------------------------------------------------
NEGATIVE_PROMPT = (
    "worst quality, low quality, early, old, score_1, score_2, score_3, "
    "cartoon, graphic, painting, crayon, graphite, abstract, glitch, deformed, "
    "mutated, ugly, disfigured, long body, bad anatomy, bad hands, "
    "missing fingers, extra fingers, extra digits, fewer digits, cropped, "
    "very displeasing, artist name, blurry, jpeg artifacts, lowres, censor"
)

# 位置に依存しない「先頭」タグ(品質 / メタ / 安全 / 人数)。
# 出力の並べ替えはしないが、検出したアーティスト名(@)を「先頭タグ群の直後・
# キャラブロックの頭」へ差し込むときに、この集合で先頭の境目を見つける。
# score タグは Anima のモデルカードに従いアンダースコアを保持する。
STATIC_TAGS = [
    # 品質
    "masterpiece", "best quality", "high quality", "amazing quality",
    "very aesthetic",
    "score_9", "score_8", "score_7",
    # 解像度 / メタ
    "absurdres", "highres",
    # 年代 / 時期
    "newest", "recent", "year 2025", "year 2024",
    # 安全
    "safe", "sensitive", "nsfw", "explicit",
    # 人数 / 相互作用の先頭
    "1girl", "2girls", "3girls", "multiple girls",
    "1boy", "2boys", "multiple boys", "1other",
    "solo", "duo", "male focus",
]
