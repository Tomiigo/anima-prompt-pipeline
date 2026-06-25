# anima-prompt-pipeline

**日本語で書いたシーンの説明を、Anima(Danbooru / Gelbooru 系)の画像生成プロンプトへ変換するツールです。**

「広場で、赤い服の女性が仰向けに寝そべって笑っていて、その隣で青い服の男性がしゃがんでいて……」——こんな日本語の文章を入れると、Anima がそのまま読める英語のタグ列＋自然文プロンプトを組み立てて出力します。

仕組みは **3段リレー**。各段は独立していて、前の段が出したファイルだけを次の段が読みます。

```
  日本語の文章 (inputs/ に置いたテキスト)
      │
      ▼  ① 抽出      ローカルの Gemma-4(llama.cpp サーバー)が
      │              文章を読み、構造化ブロックに分解する
      ▼  ② 正規化    辞書(booru_dict.json)で、表記ゆれを正しい
      │              Danbooru タグに直し、存在しないタグを記録する
      ▼  ③ 組み立て  決まった並び順で最終プロンプトを組み立てる
      │
      ▼
  output.txt (POSITIVE / NEGATIVE)
```

②と③は **Python の標準ライブラリだけ** で動きます(追加インストール不要)。
①だけ、文章を読む頭脳として **ローカル LLM(Gemma-4)** を llama.cpp で動かします。

---

## 必要なもの

1. **Python 3**(3.9 以降くらい。標準ライブラリのみ使用)
2. **llama.cpp** と、その上で動かす **Gemma-4 の gguf モデル**(①で使う) → 導入は [docs/llama_cpp_setup.md](docs/llama_cpp_setup.md)
3. **booru_dict.json**(②で使う辞書)。**このリポジトリには同梱していません。** 作り方は [docs/dictionary.md](docs/dictionary.md)(ビルダー `tools/build_booru_dict.py` を同梱)
4. **`curl`**(クイックスタートのスクリプトが使用。Ubuntu なら `sudo apt install curl`)

---

## クイックスタート（推奨）

`anima_pipeline_oneshot.sh` は、**「llama-server を起動 → パイプライン実行 → サーバー停止」を1コマンドで** 行うラッパーです。サーバーは実行中だけ起動し、終わったら停止して VRAM を解放するので、直後の画像生成(Anima)と VRAM を奪い合いません。

1. `booru_dict.json` を用意し、リポジトリのルートに置く([docs/dictionary.md](docs/dictionary.md))。
2. スクリプト冒頭の4項目を自分の環境に合わせて編集する: `LLAMA_DIR` / `MODEL_PATH` / `PIPELINE_DIR` / `SERVER_CMD`。
3. 変換したい日本語の文章を、**`inputs/` にテキストファイル1つ** として置く(ファイル名は任意。詳細は [inputs/README.md](inputs/README.md))。
4. 実行する:

```bash
chmod +x anima_pipeline_oneshot.sh
./anima_pipeline_oneshot.sh                 # 既定(rating=safe)で実行
./anima_pipeline_oneshot.sh --rating safe   # 追加引数は run_pipeline.py へ素通し
```

ルートに **`output.txt`**(`POSITIVE:` と `NEGATIVE:` の最終プロンプト)が出力されます。

---

## 使い方（手動でパイプラインを回す）

ラッパーを使わず、自分でサーバーを立てて回すこともできます。先に [docs/llama_cpp_setup.md](docs/llama_cpp_setup.md) の手順で `llama-server` を起動し、**別のターミナルから**:

```bash
python3 run_pipeline.py \
    --input inputs/あなたの入力.txt \
    --dict booru_dict.json \
    --sys system_prompt.txt \
    --out output.txt
```

### レーティングを指定する

```bash
python3 run_pipeline.py --input inputs/あなたの入力.txt --dict booru_dict.json \
    --sys system_prompt.txt --rating safe
```

`--rating` は `safe` / `sensitive` / `nsfw` / `explicit` のいずれか。省略時は `safe`。

> サンプリングについて: 抽出はブレないほうが良いので、温度の既定を低め(`--temp 0.1`)にしています。完全に決定的にしたい場合は `--temp 0`、逆に表現の揺らぎが欲しい場合は上げてください(`--top-p` `--top-k` も指定可)。

### サーバーを使わずに試す（配線テスト）

①の抽出結果(構造化ブロック)を手元に持っている場合は、`--from-extracted` で①を飛ばせます。サーバーを立てずに②③だけ回せるので、動作確認に便利です。

```bash
python3 run_pipeline.py --from-extracted extracted.txt \
    --dict booru_dict.json --sys system_prompt.txt --out output.txt
```

### 出力されるファイル

| ファイル | 中身 |
|----------|------|
| `output.txt` | 最終プロンプト(これを画像生成に貼る) |
| `extracted.txt` | ① Gemma が出した構造化ブロック(中間) |
| `normalized.txt` | ② 辞書で正規化した中間データ |
| `unresolved.log` | ② 辞書(canonical)に無かったタグの一覧。削除はせず記録だけする |

`unresolved.log` に大量にタグが出ているときは、辞書に無い綴り or 存在しないタグを Gemma が作った可能性があります。確認の手がかりにしてください。

> 入力ファイル・`output.txt`・上記の中間ファイルは、利用者ごとに中身が異なるため Git 追跡対象外(`.gitignore` 済み)です。

---

## 相互干渉の写像表（`interaction_map.json`）

複数キャラの**相互干渉**（見つめ合う・手をつなぐ・服を引く等）は、Anima が苦手とする部分です。自然文で「向かい合う」と書くと向きが正しく出る確率は半分程度しかありません。一方で Anima は、二人の配置そのものを一語で表す**正準タグ**（`eye contact`、`holding hands`、`clothes pull` 等）を、画像とともに学習しています。これらは自然文よりはるかに確実に効きます。

`interaction_map.json` は、②正規化の段で、`[INTERACTION]` の干渉表現をこの**確実な正準タグへ確定変換**するための写像表です。たとえば:

- `facing each other` → `facing another, eye contact`（向きを固定する定番の手）
- `pulling shirt` → `clothes pull`
- `patting head` → `hand on another's head, headpat`

各エントリには Danbooru の件数に基づく**信頼度**（high / medium / low）が付いており、`low`（件数が少なく不確実）の干渉を変換したときは、パイプライン実行時に注意メッセージを出します。写像表に無い表現はそのまま素通しします。新しい言い回しに出会ったら、`interaction_map.json` に1行足すだけで拡張できます。

> 注意: タグは「動作」と「相対的な向き」は確実に効かせますが、**「どちらがどちらに」**という主従までは一語では決まりません。誰が能動かを確実に固定するには、`size difference` / `height difference` で寄せるか、領域指定（リージョナルプロンプト）と組み合わせる必要があります。

---

## ファイル一覧

| パス | 役割 |
|------|------|
| `anima_pipeline_oneshot.sh` | llama-server 起動 → 実行 → 停止 を1コマンドで行うラッパー(VRAM 解放つき) |
| `run_pipeline.py` | 3段を自動でつなぐ配線役(標準ライブラリのみ) |
| `normalize.py` | ② 正規化+実在検証(標準ライブラリのみ) |
| `interaction_map.json` | ② 相互干渉の写像表 |
| `assemble.py` | ③ 最終プロンプトの組み立て(標準ライブラリのみ) |
| `system_prompt.txt` | ① 抽出用のシステムプロンプト |
| `inputs/` | 変換したい文章を置く場所(1ファイル。[inputs/README.md](inputs/README.md)) |
| `tools/build_booru_dict.py` | `booru_dict.json` を CSV から作るスクリプト([docs/dictionary.md](docs/dictionary.md)) |
| `docs/llama_cpp_setup.md` | llama.cpp の導入・起動・VRAM 配分・トラブル対処 |
| `docs/dictionary.md` | 辞書の作り方(CSV 取得・ビルド・構造) |

---

## カスタマイズと自己責任について

このツールは、**rating を `safe` 以外(`sensitive` / `nsfw` / `explicit`)にも指定できます。** また、①で使う LLM(Gemma の版を含む)を何にするかは、すべて利用者が自分で選びます。

はっきり書いておきます。

- 作者も、この README の作成を手伝った AI も、**無規制版モデルの使用や、プロンプトから `safe` を外すことを、推奨したり誘導したりはしません。** そのためのやり方も説明しません。
- ですが、そうしたカスタマイズを **技術的に不可能にもしていません。**

だから、その先は **完全に自己責任** です。出力した画像で何が起きても、それはあなたが選んだ結果であって、ツールや作者が肩代わりするものではありません。**お住まいの地域の法律と、画像を投稿・利用する各プラットフォームの規約を、必ず自分で守ってください。**

---

## 謝辞・出典

- タグの並び順や記法は、Crody 氏の Anima 画像生成ガイドを主な参考にしています。
- `booru_dict.json` の元データ(Danbooru / Gelbooru のタグ・別名 CSV)は、**DeepGHS** さんが公開しているデータセット <https://huggingface.co/datasets/deepghs/site_tags> から取得しました。整備・公開に感謝します。
- 元データの利用条件は、上記データセットおよび Danbooru / Gelbooru 各サイトの規約に従ってください。
