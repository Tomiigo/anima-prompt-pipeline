###謝辞
このスクリプトを構築するにあたっては、Crodyさん(https://civitai.com/user/Crody)
の知見をほぼそのまま参考にさせて頂いています。

# anima-prompt-pipeline

日本語で書いたプロンプトを、ローカルの **Gemma**(llama.cpp 経由)で英語に翻訳し、
**Danbooru/Gelbooru 由来の辞書**と **Anima 用のルール**を使って、画像生成モデル
**[Anima](https://huggingface.co/circlestone-labs/Anima)** が理解しやすいプロンプトへ
整形するツールです。誰でもテキストだけで Anima を扱えるようにすることを目的にしています。

次の流れで動きます。

1. **翻訳** — 日本語のプロンプトを Gemma で英語にする
2. **生成** — Gemma が Anima 用ルール(`prompts/anima_rules.txt`)の順序で、Anima のタグ列(+任意の自然文)を書く
3. **スナップ補正** — 出てきたタグを、完璧な辞書で実在タグへ寄せる(別名→正規形、綴りの僅かなズレは近い実在タグへ)

加えて、プロンプト本文に書かれた**キャラ名・作品名・アーティスト名を別辞書から完全一致で拾い**、
キャラブロックの頭へ差し込む機能があります(任意・既定で有効。アーティストには `@` を付与)。
ネガティブプロンプトも併せて出力します。

> 画像生成そのものは ComfyUI 側で行います。このツールが出すのは「Anima 用に整えた
> プロンプト文字列」です。

---

## 必要なもの(前提)

このリポジトリは「Anima 専用の層(ルール・辞書ビルダー・パイプライン)」だけを提供します。
以下は各自で用意してください(インストール手順は各公式に従ってください)。

- **llama.cpp**(`llama-server`):https://github.com/ggml-org/llama.cpp
- **ComfyUI**(画像生成):https://github.com/comfyanonymous/ComfyUI
- **Anima 本体 + テキストエンコーダ + VAE**:https://huggingface.co/circlestone-labs/Anima
  （`anima-base-v1.0.safetensors` / `qwen_3_06b_base.safetensors` / `qwen_image_vae.safetensors`）
- **チャット用の Gemma4**:`gemma-4-26B-A4B-it` を GGUF 形式で(量子化は `Q4_K_M` を選ぶ)。日本語→英語の翻訳と、プロンプトの整形に使います。本ツールが使う言語モデルは、この Gemma だけです。
- **Python 3.10+** と、`anima_pipeline/requirements.txt` の依存。

> モデルの重みはこのリポジトリに含めていません。各配布元から取得してください。

---

## タグ CSV について(同梱していません)

辞書の元データ(タグ CSV)は **同梱していません**。データの出所を尊重するため、
Hugging Face の **John Steward(HDiffusion)** さんから各自取得してください。

  https://huggingface.co/HDiffusion

必要なのは Danbooru のタグ数データと Gelbooru のタグデータです。詳しい置き場所と
ファイル名・スキーマは [`anima_pipeline/data/raw/README.md`](anima_pipeline/data/raw/README.md)
を見てください。**ビルド済みの辞書も同梱していません**(各自でローカル生成します)。

---

## セットアップ

> 作業は **展開したリポジトリのルート**(`anima_pipeline/` がある階層)で、かつ
> **ホーム配下**で行ってください(`/home` 直下や system ディレクトリは避ける)。
> zip は**ダウンロードした場所**(多くは `~/Downloads`)を指定して展開します。例:
> `unzip ~/Downloads/anima-prompt-pipeline.zip -d ~ && cd ~/anima-prompt-pipeline`
> その後の `python3 -m venv venv` は、この**リポジトリのルートの中**で実行します。

```bash
# 1) 仮想環境と依存(Ubuntu 等では 'python' ではなく python3 を使う)
python -m venv venv
source venv/bin/activate            # 先頭が (venv) になる。Windows は venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r anima_pipeline/requirements.txt

# 2) タグ CSV を data/raw/ に置く(HDiffusion から取得。上記参照)

# 3) 辞書を作る(一般=本体辞書、アーティスト/キャラ=別辞書。後者2つは任意)
cd anima_pipeline
python ../build_anima_dictionary.py \
  --danbooru data/raw/danbooru.csv --gelbooru data/raw/gelbooru.csv \
  --keep-categories 0 --min-count 10 --out-dir data/dict
python ../build_anima_dictionary.py \
  --danbooru data/raw/danbooru.csv --gelbooru data/raw/gelbooru.csv \
  --keep-categories 1 --min-count 50 --out-dir data/dict_artist
python ../build_anima_dictionary.py \
  --danbooru data/raw/danbooru.csv --gelbooru data/raw/gelbooru.csv \
  --keep-categories 3,4 --min-count 100 --out-dir data/dict_char
```

> **Python のコマンド**(`pip`、`python run.py` など)は
> **venv を有効化した状態**で実行します。新しいターミナルを開いたら毎回
> `source venv/bin/activate` してください(有効化後は `python` がこの venv のものになります)。
> 次の「サーバーの起動」で使う `llama-server` は **Python とは別のプログラム**なので、
> venv の有効化は不要です(別のターミナルでそのまま起動します)。

> 一般辞書はスナップ補正(別名→正規形・綴り寄せ)に、アーティスト/キャラ辞書は本文中の名前の
> 完全一致検出に使います。アーティスト/キャラ辞書は任意で、無ければその検出が無効になるだけです。
> 詳しくは `anima_pipeline/README.md`。

### サーバーの起動

起動するサーバーは **Gemma を 1 つだけ**です(:8080)。**別のターミナルの窓**で、起動したまま動かし続けます。先に注意点を 3 つ。

- この `llama-server` は **venv とは無関係の別プログラム**です。`source venv/bin/activate` は**不要**です(`python` のコマンドではないため。別のターミナルでそのまま起動します)。
- `llama-server` は**フルパス**で呼びます。下の例の `~/llama.cpp/build/bin/llama-server` は、llama.cpp を `~/llama.cpp` に置いてビルドした場合の場所です。**違う場所なら、そこに合わせてください**。`llama-server: コマンドが見つかりません` と出たら、フルパスになっていないサインです。
- コマンドは**1行**で入力します(折り返して貼ると行末が切れます)。`-ot` は**削らないでください**(外すと out of memory になります)。

1. ターミナルの**新しいウィンドウ**を開く。

2. **チャット用 Gemma4(:8080)**を起動する。次を 1 行で入力する(`-ot` を必ず付ける):

   ```bash
   ~/llama.cpp/build/bin/llama-server -m ~/llama.cpp/models/gemma-4-26B-A4B-it-Q4_K_M.gguf --port 8080 -c 8192 -ngl 99 -ot "\.ffn_(up|down|gate)_exps\.=CPU" -fa on --jinja --reasoning-budget 0
   ```

   `server is listening on http://127.0.0.1:8080` と出れば成功。**この窓は閉じずに**置いておく。

> `-ot ...` は、Gemma の巨大な部分を RAM に逃がして VRAM を数 GB に抑えるためのもので、付けないと out of memory になります。`--reasoning-budget 0` は Gemma の「思考(reasoning)」出力を止めるためのものです。これを付けないと、Gemma が答えの前に長い思考文を書き、翻訳や生成の本体(JSON)に届く前に出力が途切れたり、思考文がそのまま混じったりします。モデルの置き場所が上と違うときは、`-m` の後ろのパスも自分の場所に直してください。

---

## つまずきやすい点(特に Linux / 日本語環境)

セットアップでよく引っかかる点です。先に目を通しておくと安全です。

- **`python` ではなく `python3`**:Ubuntu などには既定で `python` がありません。venv 作成は
  `python3 -m venv venv` で行います。有効化(`source venv/bin/activate`)後は、その venv の
  `python`/`pip` が使えます。新しいターミナルを開いたら毎回 `source venv/bin/activate` を。
- **zip はダウンロードした場所から展開する**:`unzip` はカレントディレクトリを見るので、保存先を
  指定します。場所が分からなければ `find ~ -iname '*anima*'` で探せます。
- **日本語環境のフォルダ名**:ダウンロードフォルダは英語の `~/Downloads` ではなく **`~/ダウンロード`**
  になっていることがあります(`~/ドキュメント` なども同様)。パスはこの実際の名前で指定します。
- **`llama-server` のコマンドは1行で**:複数行に折って貼ると行末(特に最後の引数の値)が切れて
  `expected value for argument` 等になります。1 行にまとめて実行してください。
- **`out of memory` と出たら(Gemma の起動)**:`-ngl 99` だけだと VRAM が足りずに止まります。
  上の起動コマンドのように `-ot "\.ffn_(up|down|gate)_exps\.=CPU"` を必ず付けてください。これで、
  毎回使う部分だけ GPU に残し、巨大な部分を RAM に逃がせます(VRAM 数 GB 程度まで下がります)。
  それでも厳しければ `-ngl 0` を付けると Gemma を完全に CPU で動かせます(GPU を ComfyUI に空けられます。
  翻訳は 1 回だけの処理なので、CPU でも実用的です)。
- **出力が箇条書きの「思考文」になる / 途中で切れる**:Gemma が答えの前に長い思考(reasoning)を
  書き出し、本体(英訳や JSON)に届く前に切れたり、思考文がそのまま出力に混じったりする状態です。
  Gemma の起動コマンドに **`--reasoning-budget 0`** を足してください(思考を止め、直接答えさせる)。
  これでも思考が止まらない場合は、`--chat-template-kwargs "{\"enable_thinking\": false}"` も併せて付けます。

---

## 使い方

```bash
cd anima_pipeline
python run.py "茶髪の少女が教室の窓辺に立っている"
python run.py "kantoku風の絵柄で、教室の窓辺に立つ少女"      # 本文中のアーティスト名を自動で @付与
python run.py --tags "@kantoku" "公園のベンチに座る二人の少女"  # アーティストを手動指定
python run.py --tags "fern" "本を読む少女"                     # 一般辞書にないキャラ名などを手動指定
```

出力は「英訳 → Anima プロンプト → ネガティブプロンプト」の順に表示されます。Anima プロンプトと
ネガティブプロンプトを、ComfyUI の Anima ワークフローにそれぞれ貼って生成します。

---

## 最初に編集する項目

- `anima_pipeline/config.py`:`CHAT_URL` のポート、スナップのしきい値(`SNAP_MAX_WORDS` /
  `SNAP_FUZZY_CUTOFF`)、`USE_ARTIST_DICT` などの挙動。
- サーバー起動コマンドの **モデルファイルのパス**(各自の置き場所)。

---

## ライセンスとクレジット

[`NOTICE.md`](NOTICE.md) を必ず読んでください。要点:

- **Anima** は CircleStone Labs の **非商用ライセンス**です(モデル自体の商用利用は不可)。
- タグデータは **HDiffusion(John Steward)** さんの公開データに由来します(同梱せず、各自取得)。
- `anima_pipeline/prompts/anima_rules.txt` の**タグの並び順(構成)**は、CivitAI の **Crody**(Team-C)さんの
  プロンプト解説に基づいています(Anima 向けに言い換え・調整)。記事 https://civitai.com/articles/19107 /
  作者 https://civitai.com/user/Crody 。そのほかの知見は **dskjal.com** などコミュニティの検証と
  Anima 公式モデルカードに基づきます。詳細と謝辞は [`NOTICE.md`](NOTICE.md)。
- このリポジトリの**コードは MIT ライセンス**です([`LICENSE`](LICENSE) を参照)。MIT が対象とするのは
  このリポジトリのコード(およびルール/お手本)のみで、**Anima モデルやタグデータには適用されません**
  (それぞれ別ライセンス。`NOTICE.md` 参照)。

---

## もっと詳しく

- パイプラインの仕組み・全コマンド:[`anima_pipeline/README.md`](anima_pipeline/README.md)
- ルールとお手本の編集ガイド:[`anima_pipeline/prompts/README.md`](anima_pipeline/prompts/README.md)
