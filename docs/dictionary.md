# 辞書（`booru_dict.json`）の作り方

このツールの **②正規化** は、抽出されたタグの表記ゆれを正しい Danbooru タグに直し、存在しないタグを記録します。そのために辞書 `booru_dict.json` を使います。

**この辞書はリポジトリに同梱していません**（巨大で、各自がビルドするものだからです）。下の手順で作ってください。ビルダー本体は `tools/build_booru_dict.py` に入っています。

## 元データ（CSV）の取得

辞書は、**Danbooru と Gelbooru のタグ情報（タグ本体と別名）** から作ります。元データの CSV は、**DeepGHS さんが HuggingFace で公開しているデータセット** から取得できます。

<https://huggingface.co/datasets/deepghs/site_tags>

> 元データを整備・公開してくださっている **DeepGHS** に感謝します。このツールの②（正規化・実在検証）は、この公開データがあって初めて成り立っています。

データセットの「Files and versions」のうち、対象は次の2フォルダです。

- **danbooru.donmai.us**
- **gelbooru.com**

どちらのフォルダにも7種類のファイルがありますが、辞書作りに必要なのは **`tag_aliases.csv`** と **`tags.csv`** の **2つだけ** です。これらをダウンロードし、それぞれ次の名前に変更してください。

```
Danbooru_tags.csv
Danbooru_aliases.csv
Gelbooru_tags.csv
Gelbooru_aliases.csv
```

## ビルド

リポジトリのルートで、4ファイルを `tools/build_booru_dict.py` に渡します。

```bash
python3 tools/build_booru_dict.py \
    --danbooru-tags    Danbooru_tags.csv \
    --danbooru-aliases Danbooru_aliases.csv \
    --gelbooru-tags    Gelbooru_tags.csv \
    --gelbooru-aliases Gelbooru_aliases.csv \
    --out booru_dict.json
```

`booru_dict.json` ができたら、`run_pipeline.py` の `--dict` に渡してください（`anima_pipeline_oneshot.sh` を使う場合は、ルートに `booru_dict.json` を置いておけば自動で参照します）。

## CSV の列について

ビルダーが期待する列は次の通りです（取得したファイルの列名がこれと違う場合は、列名を合わせてください）。

- **タグ一覧** … Gelbooru: `name`, `type`, `count` / Danbooru: `name`, `category`, `post_count`, `is_deprecated`
- **別名一覧** … `alias`, `tag`

## 出来上がる辞書の構造

辞書は2部構成です。

- **canonical**（実在検証用）… 投稿数のしきい値を超えた正規タグだけが入ります。Gemma が作った存在しないタグを検出する基準になります。
- **aliases**（別名表）… 表記ゆれを正しいタグに直すための対応表。実在検証とは切り離し、広めに保持します。

カテゴリごとの件数しきい値や設計方針の詳細は、`tools/build_booru_dict.py` の冒頭コメントにあります。

## 利用条件

元データの利用条件は、**HuggingFace のデータセット、および大元の Danbooru / Gelbooru の規約**に従ってください。
