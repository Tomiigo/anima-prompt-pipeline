# data/raw — タグ CSV はここに置きます(同梱していません)

このリポジトリには、辞書の元になるタグ CSV を **同梱していません**。
データの出所を尊重するため、各自で配布元から取得してください。

## 取得先

タグ数データは Hugging Face の **John Steward(HDiffusion)** さんが公開しています。
こちらから入手してください:

  https://huggingface.co/HDiffusion

必要なのは次の2種類です(HDiffusion のデータセット一覧から探せます):

- **Danbooru のタグ数データ**(historical-danbooru-tag-counts 系)
- **Gelbooru のタグデータ**(gelbooru-tags 系)

ダウンロードした CSV を、このフォルダに次の名前で置いてください
(別名なら、ビルド時の `--danbooru` / `--gelbooru` 引数をその名前に合わせます):

```
data/raw/danbooru.csv
data/raw/gelbooru.csv
```

## CSV の想定スキーマ

`build_anima_dictionary.py` は次の4列を前提にしています(列名はこの通り):

- `tag_string`     … アンダースコア区切りの正規タグ
- `category_int64` … 0=general, 1=artist, 3=copyright, 4=character, 5=meta
- `count_int64`    … 投稿数(カンマ入りの文字列でも可。スクリプトが数値化します)
- `alias_string`   … カンマ区切りの別名(無い場合は空 / null)

**ヘッダー行の有無は自動判定されます。** HDiffusion の生CSV はヘッダーが無いことが
ありますが、そのままで動きます(スクリプトが上記の列名を自動で割り当てます)。
自分でヘッダー(`tag_string,category_int64,count_int64,alias_string`)を付けても構いません。
いずれの場合も、**列の順序が上の通り**であることだけ確認してください。

## 次の手順

CSV を置いたら、リポジトリの README の「辞書を作る」に従って `data/dict/`(一般タグ辞書。
必要ならアーティスト用 `data/dict_artist/`、キャラ/作品用 `data/dict_char/` も)を
ローカルで生成します(これらも同梱していません)。
