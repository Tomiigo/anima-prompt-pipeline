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

### 起動コマンド

下のコマンドをそのまま打てば起動します。パスやファイル名は自分の環境に合わせて変えてください。
**--fit-target 6144**は、VRAMの空き容量をこれ以下にするな、という引数です。

```bash
cd /home/user/llama.cpp
./build/bin/llama-server -m models/gemma-4-12B-it-Q4_K_XL.gguf \
  --fit on \
  --fit-target 6144 \
  -c 4096 \
  -ctk q8_0 -ctv q8_0 \
  -b 256 \
  --flash-attn on \
  -t 8 \
  --jinja \
  --chat-template-kwargs '{"enable_thinking":false}' \
  --host 127.0.0.1 --port 8080
```

各引数の意味:

| 引数 | 意味 |
|------|------|
| `-m models/....gguf` | 使うモデルファイルの場所。自分の gguf に合わせて変える。 |
| `--fit on` |  llama.cpp に載せ方を自動調整させる。 |
| `-c 8192` | **コンテキスト長(トークン数)。後述の注意あり。** |
| `--fit-target 1536` | fit の調整の目安を渡す。 |
| `-b 512` | バッチサイズ。まずはこのままで OK。 |
| `--flash-attn on` | 計算を速く・省メモリにする仕組みを使う。 |
| `-t 8` | CPU のスレッド数。自分の CPU に合わせる(コア数くらい)。 |
| `--jinja` | モデル同梱のチャットテンプレートを使い、`system` ロールを正しく扱わせる(Gemma-4 は system ロール対応)。 |
| `--chat-template-kwargs '{"enable_thinking":false}'` | 思考モードを OFF にする。構造化抽出では思考が混ざると答えが思考チャンク側に入り本文が空になることがあるため、OFF が安定。 |
| `--host 127.0.0.1` | 待ち受け先。`127.0.0.1` は「自分の PC の中だけ」。外には公開されません。 |
| `--port 8080` | 待ち受けポート番号。スクリプト側の既定も `8080`。 |

末尾の `--jinja` と `--chat-template-kwargs '{"enable_thinking":false}'` は、Gemma-4 に**決まったブロックを正確に出させる**ための設定です（構造化抽出では思考は OFF が安定）。これらの対応状況は llama.cpp のビルドによって差があるので、**起動したら一度動作確認してください**（パイプラインを回して、出力が空にならず最終プロンプトが出ること）。古いビルドで `--chat-template-kwargs` が受け付けられない場合は、その行を外せば従来通り動きます。

> トラブル対処: 出力に `<unused49>` のような無意味なトークンが大量に出る既知の不具合があります。その場合は (1) llama.cpp を最新ビルドに更新、(2) 画像を一切渡さない運用なら起動時に `--no-mmproj` を足す、(3) サーバーを再起動する、を試してください。

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

> サンプリングについて: 抽出はブレないほうが良いので、温度の既定を低め(`--temp 0.1`)にしています。完全に決定的にしたい場合は `--temp 0`、逆に表現の揺らぎが欲しい場合は上げてください(`--top-p` `--top-k` も指定可)。

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

### システムプロンプトと「思考モード」

抽出用のプロンプトは `system_prompt.txt` の1本です。

Gemma-4 には「思考(thinking)モード」がありますが、**この用途（決まったブロックを正確に出す構造化抽出）では思考は OFF が安定します**。思考モードは llama.cpp 側のフラグで制御するのが正しく、プロンプトに `<|think|>` のようなトークンを手書きする必要はありません（むしろ誤動作の元）。起動コマンドで `--chat-template-kwargs '{"enable_thinking":false}'` を渡してください（下の「怖くない！ llama.cpp」参照）。

> もし複雑なシーンで Gemma に一度考えさせたい場合だけ、`enable_thinking` を `true` にします。その場合、出力の前に思考チャンクが付くことがあり、`--out` に渡る前にそれを取り除く処理が別途必要になります。通常は OFF のままで構いません。

---

## 相互干渉の写像表（`interaction_map.json`）

複数キャラの**相互干渉**（見つめ合う・手をつなぐ・服を引く等）は、Anima が苦手とする部分です。自然文で「向かい合う」と書くと向きが正しく出る確率は半分程度しかありません。一方で Anima は、二人の配置そのものを一語で表す**正準タグ**（`eye contact`、`holding hands`、`clothes pull` 等）を、画像とともに学習しています。これらは自然文よりはるかに確実に効きます。

`interaction_map.json` は、②正規化の段で、`[INTERACTION]` の干渉表現をこの**確実な正準タグへ確定変換**するための写像表です。たとえば:

- `facing each other` → `facing another, eye contact`（向きを固定する定番の手）
- `pulling shirt` → `clothes pull`
- `patting head` → `hand on another's head, headpat`

各エントリには Danbooru の件数に基づく**信頼度**（high / medium / low）が付いており、`low`（件数が少なく不確実）の干渉を変換したときは、パイプライン実行時に注意メッセージを出します。写像表に無い表現はそのまま素通しします（従来通り別名解決・実在検証にかかる）。新しい言い回しに出会ったら、`interaction_map.json` に1行足すだけで拡張できます。

> 注意: タグは「動作」と「相対的な向き」は確実に効かせますが、**「どちらがどちらに」**という主従までは一語では決まりません。誰が能動かを確実に固定するには、`size difference` / `height difference` で寄せるか、領域指定（リージョナルプロンプト）と組み合わせる必要があります。

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
| `interaction_map.json` | ② 相互干渉の写像表(下記参照) |
| `assemble.py` | ③ 最終プロンプトの組み立て(標準ライブラリのみ) |
| `system_prompt.txt` | ① 抽出用のシステムプロンプト |
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
