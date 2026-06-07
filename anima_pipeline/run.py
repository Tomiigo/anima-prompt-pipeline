"""日本語 -> Anima プロンプトパイプラインの CLI エントリポイント。

  # 日本語プロンプトを変換する:
  python run.py "茶髪の少女が教室の窓辺に立っている"

  # 一般辞書の外にあるキャラ / アーティストタグを注入する:
  python run.py --tags "fern,@kantoku" "二人の少女が公園のベンチに座っている"

サーバーは Gemma(config.CHAT_URL, 既定 127.0.0.1:8080)だけを使う。
起動方法は README.md を参照。
"""
from __future__ import annotations
import argparse
import sys

import config


def cmd_run(ja_prompt: str, extra_tags: list[str]) -> None:
    # 一般辞書が未作成のときは、長いトレースバックではなく短い案内を出す。
    if not config.ALIAS_MAP.exists():
        print(
            f"辞書が見つかりません: {config.ALIAS_MAP}\n"
            f"先に一般タグ辞書を作ってください(README の「辞書を作る」)。anima_pipeline/ の中で:\n"
            f"  python ../build_anima_dictionary.py --danbooru data/raw/danbooru.csv "
            f"--gelbooru data/raw/gelbooru.csv --keep-categories 0 --min-count 10 --out-dir data/dict",
            file=sys.stderr,
        )
        sys.exit(1)

    import requests
    from pipeline import AnimaPipeline
    try:
        pipe = AnimaPipeline()
        res = pipe.run(ja_prompt, extra_tags=extra_tags)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        print(
            f"Gemma サーバーに接続できません({config.CHAT_URL})。\n"
            f"別のターミナルで llama-server を起動してから、もう一度実行してください"
            f"(README の「サーバーの起動」を参照)。",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\n--- English ---")
    print(res["english"])
    print("\n--- Anima prompt ---")
    print(res["prompt"])
    if res.get("negative"):
        print("\n--- Negative prompt ---")
        print(res["negative"])
    if res["issues"]:
        print("\n[!] validation issues: " + "; ".join(res["issues"]), file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Japanese -> Anima prompt pipeline "
                    "(translate -> generate -> dictionary snap-correction)")
    ap.add_argument("input", nargs="?", help="Japanese prompt to convert")
    ap.add_argument("--tags", default="",
                    help="comma-separated extra tags to inject (characters, "
                         "@artists) that are not in the general dictionary")
    args = ap.parse_args()

    if not args.input:
        ap.print_help()
        return

    extra = [t.strip() for t in args.tags.split(",") if t.strip()]
    cmd_run(args.input, extra_tags=extra)


if __name__ == "__main__":
    main()
