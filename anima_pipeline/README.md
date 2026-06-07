# anima_pipeline — 日本語 → Anima プロンプト変換

ローカルの **llama.cpp + Gemma 4** で、日本語の指示を Anima 用プロンプトに変換する
パイプラインです。モデルの追加学習(ファインチューニング)は行いません。日本語の指示は、
次の流れで Anima 用プロンプトになります。

1. **翻訳** — 日本語の指示を Gemma で英語にする(`generate.ChatClient.translate_ja_en`)
2. **生成** — Gemma が `prompts/anima_rules.txt` の順序で、Anima のタグ列(+任意の自然文)を書く
3. **スナップ補正** — 出てきたタグを、完璧な辞書で実在タグへ寄せる(`postprocess.Normalizer`:
   別名→正規形、`_`→スペース、綴りの僅かなズレは近い実在タグへ)
4. **検出名の注入** — 本文中のキャラ名・作品名(シリーズ)・アーティスト名を辞書から完全一致で
   拾い、キャラブロックの頭へ差し込む(キャラ/作品名は素のタグ、アーティストは `@` 付き)
5. **ネガティブプロンプト** — `config.NEGATIVE_PROMPT` を結果に添える

辞書(数万語)は Gemma に渡すのではなく、**後段の補正**に使い切ります。タグの全リスト
(約 7 万語)は大きすぎて、生成のときに Gemma へ一度に渡すことはできません。そこで本ツールは、
候補をあらかじめ絞って Gemma に選ばせるのではなく、**Gemma に自由にタグを書かせてから、その綴りや
言い回しのズレを辞書で実在タグへ直す**という順序にしています。Danbooru のタグをよく知っている
大きめのモデルでは、こちらのほうが扱いやすく、出力も安定します。

ルール定義は Gemma に渡す**プロンプト**で行い、`prompts/anima_rules.txt` を
ユーザー間で共有・改良していく想定です(`prompts/README.md` を参照)。

> 起動して使うサーバーは **Gemma(:8080)1 つだけ**です。

---

## フォルダ構成

```
anima_pipeline/
├── data/
│   ├── raw/          ← ここに danbooru.csv / gelbooru.csv を置く
│   ├── dict/         ← 一般タグ辞書(build_anima_dictionary.py の出力)
│   ├── dict_artist/  ← アーティスト辞書(任意・別ビルド)
│   └── dict_char/    ← キャラ/作品辞書(任意・別ビルド)
├── prompts/
│   ├── anima_rules.txt   ← 共有・改良するルール(人間が編集)
│   ├── fewshot.txt       ← お手本(任意。空でもよい)
│   └── README.md         ← 編集・共有ガイド
├── config.py        ← パス / ポート / 各種設定
├── postprocess.py   ← スナップ補正(別名→正規タグの正規化・近似一致)
├── constrain.py     ← 整形(render)と機械的な検証(validate)
├── generate.py      ← Gemma チャット/翻訳クライアント
├── pipeline.py      ← 全体オーケストレーション
└── run.py           ← CLI
```

> 元データの CSV は **`data/raw/`** に置いてください。辞書は、リポジトリのルート
> (この `anima_pipeline/` の 1 つ上の階層)にある `build_anima_dictionary.py` で作ります。
> その出力を **`data/dict/`** に置けば(既定でそこに出力されます)、本パイプラインが読み込みます。

---

## セットアップ

```bash
pip install -r requirements.txt
```

### 1. 辞書を作る

`build_anima_dictionary.py` で、`data/raw/` の CSV から辞書を生成します。用途別に
3 種類(一般 / アーティスト / キャラ・作品)を別ディレクトリへ出します。`--keep-categories`
は**カンマ区切り**で複数指定できます(0=general 1=artist 3=copyright 4=character 5=meta)。

```bash
# (a) 一般タグ -> data/dict(スナップ補正の本体。必須)
python ../build_anima_dictionary.py \
  --danbooru data/raw/danbooru.csv --gelbooru data/raw/gelbooru.csv \
  --keep-categories 0 --min-count 10 --out-dir data/dict

# (b) アーティスト -> data/dict_artist(本文中の名前を @ 付きで検出。任意)
python ../build_anima_dictionary.py \
  --danbooru data/raw/danbooru.csv --gelbooru data/raw/gelbooru.csv \
  --keep-categories 1 --min-count 50 --out-dir data/dict_artist

# (c) キャラ + 作品(シリーズ) -> data/dict_char(本文中の名前を検出。任意)
python ../build_anima_dictionary.py \
  --danbooru data/raw/danbooru.csv --gelbooru data/raw/gelbooru.csv \
  --keep-categories 3,4 --min-count 100 --out-dir data/dict_char
```

各コマンドは出力先に `alias_to_canonical.json` と `anima_tags.jsonl` を**生成します**。
どこかから入手するファイルではなく、`data/raw` の CSV から自分で組み立てるものです。
(a) はスナップ補正に必須、(b)(c) は検出機能を使うとき(`USE_ARTIST_DICT` /
`USE_CHAR_DICT` が `True`)に読み込まれます。**ファイルが無ければその検出は自動で無効**に
なるだけなので、まずは (a) だけでも動きます。

> `--min-count` は「ハードな足切り」ではなく、ほぼ全タグを残しつつノイズ(極小 count の
> いたずらタグなど)だけを削るための低い下限です。キャラ/作品はマイナーなものを残したいなら
> 値を下げ、誤検出が気になるなら上げてください。カテゴリごとに
> `--min-count-by-category "4:50,3:200"` のように上書きもできます。

### 2. Gemma サーバーを起動する

起動するのは **Gemma(:8080)1 つだけ**です。確実な手順(`llama-server` のフルパス・
`-ot` の付け方・1 行で入力・`--reasoning-budget 0`)は、トップ README の「サーバーの起動」を
見てください。要点だけ再掲:

```bash
~/llama.cpp/build/bin/llama-server -m ~/llama.cpp/models/gemma-4-26B-A4B-it-Q4_K_M.gguf \
  --port 8080 -c 8192 -ngl 99 -ot "\.ffn_(up|down|gate)_exps\.=CPU" -fa on --jinja --reasoning-budget 0
```

(モデルのファイル名・パスは各自の環境に合わせてください。VRAM が足りないときは `-ot` を必ず付け、
それでも厳しければ `-ngl 0` で完全 CPU 実行に切り替えられます。)

---

## 使い方

```bash
# 日本語プロンプトを変換する
python run.py "茶髪の少女が教室の窓辺に立っている"

# 一般辞書にないキャラ名・アーティストタグを手動で注入する
python run.py --tags "fern,@kantoku" "二人の少女が公園のベンチに座っている"
```

出力は「英訳 → Anima プロンプト → ネガティブプロンプト」の順に表示されます(検証で問題が
あれば末尾に警告)。Anima プロンプトとネガティブプロンプトを、ComfyUI の Anima ワークフローに
それぞれ貼って生成します。

Python から使う場合:

```python
from pipeline import AnimaPipeline
pipe = AnimaPipeline()
res = pipe.run("茶髪の少女が教室の窓辺に立っている", extra_tags=["fern", "@kantoku"])
print(res["prompt"])     # 最終プロンプト
print(res["negative"])   # ネガティブプロンプト
print(res["issues"])     # 検証で見つかった問題(空ならOK)
```

---

## 設定(config.py の主なつまみ)

| 変数 | 既定 | 説明 |
|---|---|---|
| `CHAT_URL` | `127.0.0.1:8080` | Gemma(llama-server)のポート |
| `TRANSLATE_FIRST` | `True` | 生成の前に JP→EN 翻訳する。`False` なら日本語のまま Gemma に渡す |
| `GEN_TEMPERATURE` | `0.4` | 生成の temperature(ルール遵守向けに低め) |
| `GEN_MAX_TOKENS` | `2048` | 出力の最大トークン |
| `SNAP_MAX_WORDS` | `4` | カンマ区切りの 1 要素がこの語数以下なら「タグ候補」としてスナップ照合。超えると説明句として原文維持 |
| `SNAP_FUZZY_CUTOFF` | `90` | ファジー一致の閾値(0–100)。高いほど誤った寄せを防ぐ。完全一致が無いタグだけがこの経路に入る |
| `STATIC_TAGS` | (一覧) | 品質/メタ/安全/人数の「先頭」タグ。検出名を差し込む位置(キャラブロックの頭)の目印に使う |
| `USE_ARTIST_DICT` | `True` | 本文中のアーティスト名を `data/dict_artist/` で完全一致検出し `@` 付きで注入(ファイルが無ければ自動で無効) |
| `ARTIST_MIN_SURFACE_LEN` | `3` | この文字数未満のアーティスト名マッチは誤検出回避のため無視 |
| `USE_CHAR_DICT` | `True` | 本文中のキャラ名・作品名を `data/dict_char/` で完全一致検出し注入(ファイルが無ければ自動で無効) |
| `CHAR_MIN_SURFACE_LEN` | `3` | この文字数未満のキャラ/作品名マッチは誤検出回避のため無視 |
| `NEGATIVE_PROMPT` | (テンプレート) | 結果に添えるネガティブプロンプト(Anima 公式ガイド準拠) |

---

## キャラ/作品・アーティストの検出(任意・本文中の固有名詞を自動で拾う)

一般タグとは別に、アーティスト・キャラ/作品を別辞書として持てます。これらは固有名詞で、
意味的な近さでの照合が効きにくいため、**本文中に書かれた名前を完全一致で拾う**方式です
(スナップ補正のファジーは使いません)。

`USE_ARTIST_DICT` / `USE_CHAR_DICT` が `True`(既定)なら、`python run.py "..."` の実行時に、
翻訳後の英文を辞書と照合し、見つかった名前をキャラブロックの頭へ注入します。仕様の順序
(キャラ名 → 作品 → `@`アーティスト)に寄せて並べます。アーティストには `@` を付け、
キャラ名・作品名は素のタグのまま入れます。

誤検出を抑えるため、(a) 一般タグと同じ綴りのもの、(b) `*_MIN_SURFACE_LEN` 文字未満の短い
表層、は固有名詞扱いしません。`--tags "fern,@kantoku"` のように手動で渡した名前は、辞書に
無くてもそのまま注入されます(辞書にあれば正規形・`@` 付与が効きます)。各検出を切るには
`config.py` で該当フラグを `False` にしてください。

> **既知の制限**:単一被写体の単純なケース向けです。複数キャラが登場する作品で、どのキャラに
> どの容姿を割り当てるか、までは制御しません。また Danbooru のキャラタグは
> `name (series)` の形を取ることがあり、本文の表記と一致しないと拾えないことがあります。
> その場合は `--tags` で明示するのが確実です。

---

## スナップ補正の考え方(なぜこの設計か)

- **別名表に一致**するもの → 正規形へ確実に置換(例 `blue eye` → `blue eyes`)。
- **タグらしい短い語**(`SNAP_MAX_WORDS` 以下)で実在タグに高確度で近いもの → その実在タグへ寄せる。
- 仕様が推奨する**長い説明句**(例 `white flowy maxi dress with layered chiffon skirt`)→
  単一タグではないので**そのまま残す**。
- 近い実在タグが**見つからない短い語**は、寄せずに残す(消さない)。Anima 側で無視されるだけです。

`SNAP_FUZZY_CUTOFF` は既定で高め(90)にしてあります。短いタグの僅かな綴り違い(例
`twintailz` 対 `twintails` はスコア ≈ 89)は**寄せずに原形のまま**通します——これは誤った
寄せを避けるための保守的な既定です。もっと積極的に直したい場合は値を下げてください。

### モデルカード由来の重要点

- Anima のテキストエンコーダーは **Qwen3-0.6B 単体**。T5-XXL は使わない。
- **アーティストタグは `@` 接頭辞が必須**(`anima_rules.txt` の TAG SELECTION の項)。
