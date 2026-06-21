# anima-prompt-pipeline

**日本語で書いたシーンの説明を、Anima(Danbooru / Gelbooru 系)の画像生成プロンプトへ変換するツールです。**

「広場で、赤い服の女性が仰向けに寝そべって笑っていて、その隣で青い服の男性がしゃがんでいて……」——こんな日本語の文章を入れると、Anima がそのまま読める英語のタグ列＋自然文プロンプトを組み立てて出力します。

仕組みは **3段リレー**。各段は独立していて、前の段が出したファイルだけを次の段が読みます。

```
  日本語の文章
      │
      ▼  ① 抽出      ローカルの Gemma-4(llama.cpp サーバー)が
      │              文章を読み、構造化ブロックに分解する
      ▼  ② 正規化    辞書(booru_dict.json)で、表記ゆれを正しい
      │              Danbooru タグに直し、存在しないタグを記録する
      ▼  ③ 組み立て  決まった並び順で最終プロンプトを組み立てる
      │
      ▼
  final_prompt.txt(POSITIVE / NEGATIVE)
```

②と③は **Python の標準ライブラリだけ** で動きます(追加インストール不要)。
①だけ、文章を読む頭脳として **ローカル LLM(Gemma-4)** を llama.cpp で動かします。

---

## 必要なもの

1. **Python 3**(3.9 以降くらい。標準ライブラリのみ使用)
2. **llama.cpp** と、その上で動かす **Gemma-4 の gguf モデル**(①で使う)
3. **booru_dict.json**(②で使う辞書)。**このリポジトリには同梱していません。** 下の「辞書の作り方」に従って、自分で作ってください(`build_booru_dict.py` を同梱しています)。

---

## 怖くない！ llama.cpp

やることは、結局 **1個のコマンドを打って、Gemma を「サーバー」として立ち上げておくだけ** です。黒い画面や英字の羅列と格闘する必要はありません。

### 「対話モード」ではなく「サーバーモード」で動かします

llama.cpp には、画面で直接おしゃべりする「対話モード」もありますが、このツールでは使いません。
代わりに **サーバーモード** を使います。一度立ち上げておけば、あとはスクリプトが勝手に Gemma とやり取りします。

サーバーを動かすプログラムの名前は **`llama-server`** です。

### llama.cpp の入手

llama.cpp 本体の入手とビルド(組み立て)は、公式リポジトリ(GitHub で「llama.cpp」を検索)の手順に従ってください。ビルドが終わると、`build/bin/llama-server` という実行ファイルができます。このツールが使うのはこれ1個です。

### 起動コマンド(これをそのまま打つ)

作者が Gemma-4-12B-it を使うときの実際のコマンドです。パスやファイル名は自分の環境に合わせて変えてください。

```bash
cd /home/user/llama.cpp
./build/bin/llama-server -m models/gemma-4-12B-it-Q4_K_XL.gguf \
  --fit on \
  -c 8192 \
  --fit-target 1536 \
  -b 512 \
  --flash-attn on \
  -t 8 \
  --host 127.0.0.1 \
  --port 8080
```

### 引数の意味(1行ずつ)

| 引数 | 意味 |
|------|------|
| `-m models/....gguf` | 使うモデルファイルの場所。自分の gguf に合わせて変える。 |
| `--fit on` |  llama.cpp に載せ方を自動調整させる。 |
| `-c 8192` | **コンテキスト長(トークン数)。後述の注意あり。** |
| `--fit-target 1536` | fit の調整の目安を渡す。 |
| `-b 512` | バッチサイズ。まずはこのままで OK。 |
| `--flash-attn on` | 計算を速く・省メモリにする仕組みを使う。 |
| `-t 8` | CPU のスレッド数。自分の CPU に合わせる(コア数くらい)。 |
| `--host 127.0.0.1` | 待ち受け先。`127.0.0.1` は「自分の PC の中だけ」。外には公開されません。 |
| `--port 8080` | 待ち受けポート番号。スクリプト側の既定も `8080`。 |

### llama.cpp のここがすごい！ 

ローカルで LLM を動かすとき、本来は「モデルのどこを GPU に載せて、どこを CPU に残すか」を自分で細かく決めなければなりません。これがけっこう難しい。
ところが **`--fit on` と `--fit-target` を付けるだけで、llama.cpp が「PC のリソースを Gemma が占領しすぎないように」よろしく取り計らってくれます。** 配分を自動で考えてくれる。もはや魔法です。
細かい数値の最適値は環境(GPU の VRAM 量など)によって変わりますが、まずは上のコマンドの値で立ち上げてみて、動けばそれでよし、です。

### コンテキスト長(`-c`)に気をつけて！

`-c` は「一度に扱える文章の長さ(トークン数)」の上限です。ここで一つ落とし穴があります。

- このツールの **システムプロンプト自体がそれなりに長い**。
- そして **日本語のような2バイト文字は、思ったよりトークンを食います。**

つまり、短い文章のつもりでも、システムプロンプト＋日本語入力＋抽出結果を合わせると、コンテキストはあっという間に膨らみます。**`-c` は大きいに越したことはありません。** 上の例では `8192` ですが、入力が長めなら増やしてください(VRAM / メモリと相談)。途中で出力が切れる・おかしくなるときは、まずここを疑ってください。

### 立ち上がったか確認する

`llama-server` を起動したまま、別のターミナルやブラウザで `http://127.0.0.1:8080` を開いてみてください。何か応答が返ってくれば、サーバーは生きています。このまま **起動しっぱなしにして**、次のパイプラインを回します。

---

## 使い方(パイプライン)

`llama-server` を起動した状態で、**別のターミナルから**:

```bash
# 通常運用: 日本語の文章を input.txt に書いておき、サーバー経由で一気に変換
python3 run_pipeline.py \
    --input input.txt \
    --dict booru_dict.json \
    --sys system_prompt.txt \
    --out final_prompt.txt
```

`final_prompt.txt` に、`POSITIVE:` と `NEGATIVE:` の最終プロンプトが書き出されます。

### レーティングを指定する

```bash
python3 run_pipeline.py --input input.txt --dict booru_dict.json \
    --sys system_prompt.txt --rating safe
```

`--rating` は `safe` / `sensitive` / `nsfw` / `explicit` のいずれか。省略時は `safe`。

### サーバーを使わずに試す(配線テスト)

①の抽出結果(構造化ブロック)をすでに手元に持っている場合は、`--from-extracted` で①を飛ばせます。サーバーを立てずに②③だけ回せるので、動作確認に便利です。

```bash
python3 run_pipeline.py --from-extracted extracted.txt \
    --dict booru_dict.json --sys system_prompt.txt --out final_prompt.txt
```

### 出力されるファイル

| ファイル | 中身 |
|----------|------|
| `final_prompt.txt` | 最終プロンプト(これを画像生成に貼る) |
| `extracted.txt` | ① Gemma が出した構造化ブロック(中間) |
| `normalized.txt` | ② 辞書で正規化した中間データ |
| `unresolved.log` | ② 辞書(canonical)に無かったタグの一覧。削除はせず記録だけする |

`unresolved.log` に大量にタグが出ているときは、辞書に無い綴り or 存在しないタグを Gemma が作った可能性があります。確認の手がかりにしてください。

### システムプロンプトは2種類

- `system_prompt.txt` … 通常版。
- `system_prompt_thinking.txt` … モデルに思考過程を促す版。モデルや好みで使い分けてください(`--sys` で指定)。

---

## 辞書の作り方

②で使う `booru_dict.json` は、**Danbooru と Gelbooru のタグ情報(タグ本体と別名)** から作ります。元データの CSV は、**DeepGHS さんが HuggingFace で公開してくださっているデータセット**<https://huggingface.co/datasets/deepghs/site_tags>から取得できます。
> 元データを整備・公開してくださっている **DeepGHS** に感謝します。このツールの②(正規化・実在検証)は、この公開データがあって初めて成り立っています。

File and versionsのフォルダのうち、以下の2つが対象です。
**danbooru.donmai.us**
**gelbooru.com**

どちらのフォルダにも、7種類のファイルがありますが、辞書を作る上で必要なのは
**tag_aliases.csv** と **tags.csv**
の**2つだけ**です。これらをダウンロードし、それぞれ
Danbooru_tags.csv
Danbooru_aliases.csv
Gelbooru_tags.csv
Gelbooru_aliases.csv
と名前を修正してください。

下のコマンドで、その4ファイルを `build_booru_dict.py` に渡します。

```bash
python3 build_booru_dict.py \
    --danbooru-tags    Danbooru_tags.csv \
    --danbooru-aliases Danbooru_aliases.csv \
    --gelbooru-tags    Gelbooru_tags.csv \
    --gelbooru-aliases Gelbooru_aliases.csv \
    --out booru_dict.json
```

`booru_dict.json` ができたら、`run_pipeline.py` の `--dict` に渡してください。

ビルダーが期待する CSV の列は次の通りです(取得したファイルの列名がこれと違う場合は、列名を合わせてください)。

- **タグ一覧** … Gelbooru: `name`, `type`, `count` / Danbooru: `name`, `category`, `post_count`, `is_deprecated`
- **別名一覧** … `alias`, `tag`

出来上がる辞書は2部構成です。**canonical**(実在検証用。投稿数のしきい値を超えた正規タグだけが入る)と **aliases**(表記ゆれを正しいタグに直す別名表。実在検証とは切り離して広めに保持)。詳しい設計方針は `build_booru_dict.py` の冒頭コメントにあります。

なお、元データの利用条件は、**HuggingFace のデータセットおよび大元の Danbooru / Gelbooru の規約**に従ってください。

---

## ファイル一覧

| ファイル | 役割 |
|----------|------|
| `run_pipeline.py` | 3段を自動でつなぐ配線役(標準ライブラリのみ) |
| `normalize.py` | ② 正規化+実在検証(標準ライブラリのみ) |
| `assemble.py` | ③ 最終プロンプトの組み立て(標準ライブラリのみ) |
| `system_prompt.txt` | ① 抽出用のシステムプロンプト |
| `system_prompt_thinking.txt` | ① 抽出用(思考促し版) |
| `build_booru_dict.py` | `booru_dict.json` を Danbooru / Gelbooru の CSV から作るスクリプト |

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
