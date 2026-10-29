# 辞書（`booru_dict.json`）の作り方

このツールの **②正規化** は、抽出されたタグの表記ゆれを正しい Danbooru タグに直し、存在しないタグを記録します。版権キャラの名前を正規のキャラタグに直し、そのキャラの既定の特徴（髪・目・耳・服など）を補うのにも使います。そのために辞書 `booru_dict.json` を使います。

**この辞書はリポジトリに同梱していません**（巨大で、各自がビルドするものだからです）。下の手順で作ってください。ビルダー本体は `tools/build_booru_dict.py` に入っています。

> 以前のビルダーで作った辞書（format 1）は使えません。②を実行すると「古い形式です」と止まるので、下の手順で作り直してください。

## 元データ

| 用途 | データ | 必須 | 利用条件 |
|------|--------|------|----------|
| タグ・別名（Danbooru） | DeepGHS `site_tags` の `danbooru.donmai.us` フォルダ | 必須 | CC-BY-4.0 |
| タグ・別名（Gelbooru） | 同 `gelbooru.com` フォルダ | 任意 | CC-BY-4.0 |
| 版権キャラの既定の特徴 | Minthy `BooruCharacters_v0.5` | 任意 | MIT |
| 版権キャラの既定の特徴（補い） | newtextdoc1111 `danbooru-tag-csv` の共起表 | 任意 | MIT |

- DeepGHS: <https://huggingface.co/datasets/deepghs/site_tags>
- Minthy: <https://huggingface.co/datasets/Minthy/BooruCharacters_v0.5>
- newtextdoc1111: <https://huggingface.co/datasets/newtextdoc1111/danbooru-tag-csv>

> 元データを整備・公開してくださっている **DeepGHS**・**Minthy**・**newtextdoc1111** の各氏に感謝します。

### DeepGHS（タグ・別名）

データセットの「Files and versions」のうち、`danbooru.donmai.us` と `gelbooru.com` の2フォルダから、**`tags.csv`** と **`tag_aliases.csv`** を取得し、次の名前に変更してください。

```
Danbooru_tags.csv
Danbooru_tag_aliases.csv
Gelbooru_tags.csv
Gelbooru_tag_aliases.csv
```

### キャラの特徴（任意）

- Minthy: `booru_characters.parquet` をそのまま置きます。**読むには `pyarrow` が要ります**（`pip install pyarrow`。必要なのはこのビルドの時だけで、②③は標準ライブラリだけで動きます）。
- newtextdoc1111: タグ対の共起回数の CSV（列 `tag_a,tag_b,count`）を置きます。

どちらも無くても辞書は作れます。その場合、版権キャラの特徴の補完は働きません（キャラ名の正規化は働きます）。

## ビルド

リポジトリのルートで、取得したファイルを `tools/build_booru_dict.py` に渡します。

```bash
python3 tools/build_booru_dict.py \
    --danbooru-tags    Danbooru_tags.csv \
    --danbooru-aliases Danbooru_tag_aliases.csv \
    --gelbooru-tags    Gelbooru_tags.csv \
    --gelbooru-aliases Gelbooru_tag_aliases.csv \
    --characters       booru_characters.parquet \
    --cooccurrence     danbooru_tags_cooccurrence.csv \
    --out booru_dict.json
```

`--gelbooru-*`・`--characters`・`--cooccurrence` は省略できます。`booru_dict.json` ができたら、`run_pipeline.py` の `--dict` に渡してください（`anima_pipeline_oneshot.sh` を使う場合は、ルートに `booru_dict.json` を置いておけば自動で参照します）。

## CSV の列について

ビルダーが期待する列は次の通りです（取得したファイルの列名がこれと違う場合は、列名を合わせてください）。

- **タグ一覧** … Danbooru: `name`, `category`, `post_count`, `is_deprecated` / Gelbooru: `name`, `type`, `count`
- **別名一覧** … `alias`, `tag`
- **共起表** … `tag_a`, `tag_b`, `count`
- **キャラ（parquet）** … `tag`, `type`, `popular_tags`

## 出来上がる辞書の構造

辞書は4部構成です（format 2）。

- **canonical**（実在検証用）… 実在する正規タグと、その種類・投稿数。**件数で捨てません**（投稿が1件以上なら残す）。投稿の少ないタグは、②のログで「低頻度」として知らせます。**アーティストのタグは入れません**（このツールはアーティストを扱わない設計です）。
- **aliases**（別名表）… 表記ゆれを正しいタグに直すための対応表。`a → b → c` の連鎖は最終のタグまで解いてあります。**それ自体が実在するタグは転送しません**（実在するタグを実在しないタグへ化けさせないため）。
- **deprecated** … Danbooru で廃止され、転送先も無いタグ。②で未解決として扱い、候補を出します。Gelbooru で使われ続けているものは実在として戻します。
- **characters** … 版権キャラ → 既定の特徴（作品・髪・目・身体・服）。②で、文章が触れていない部位だけを補います。

設計方針の詳細は、`tools/build_booru_dict.py` の冒頭コメントにあります。

## 利用条件

元データの利用条件は、**各データセット、および大元の Danbooru / Gelbooru の規約**に従ってください。
