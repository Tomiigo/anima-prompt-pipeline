#!/usr/bin/env python3
"""日本語シーン説明 → Anima プロンプト変換の3段リレーを自動で配線する(標準ライブラリのみ)。

リレー方式: 各段は独立し、前段の出力ファイルだけを次段の入力にする。
本スクリプトは段の "配線" だけを行い、本体ロジックは normalize.py / assemble.py を
subprocess で呼んで再利用する(ロジックの二重持ちを避け、各段の独立性を保つ)。

流れ:
  1. 入力(日本語)を読む。
  2. llama.cpp の OpenAI 互換エンドポイントへ system=system_prompt.txt, user=入力 を送り、
     構造化ブロック出力を extracted.txt に保存。
     (--from-extracted を渡すと、このHTTP段を飛ばして既存ファイルを使う。
      サーバ無しでの配線テストや、抽出を手動で回した結果の再利用に使える。)
  3. normalize.py を呼び、辞書で正規化して normalized.txt と unresolved.log を出す。
  4. assemble.py を呼び、output.txt を組み立てる(--rating を中継)。

使い方:
  # llama.cpp(http://localhost:8080)を使う通常運用:
  python3 run_pipeline.py --input input.txt --dict booru_dict.json \\
      --sys system_prompt.txt --out output.txt

  # サーバを使わず、既存の抽出結果から配線だけ回す:
  python3 run_pipeline.py --from-extracted extracted.txt --dict booru_dict.json \\
      --sys system_prompt.txt --out output.txt
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def call_llama(url: str, system_prompt: str, user_text: str,
               temperature: float, top_p: float, top_k: int,
               timeout: float) -> str:
    """llama.cpp の OpenAI 互換 /v1/chat/completions を叩き、本文を返す。"""
    body = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "stream": False,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    # OpenAI 互換: choices[0].message.content
    return payload["choices"][0]["message"]["content"]


def run(cmd: list[str]) -> None:
    print("  $ " + " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", type=Path, default=None,
                   help="日本語の入力テキスト。--from-extracted 指定時は不要。")
    p.add_argument("--sys", dest="sysfile", type=Path, required=True,
                   help="抽出用のシステムプロンプト(system_prompt.txt)")
    p.add_argument("--dict", dest="dictfile", type=Path, required=True,
                   help="booru_dict.json")
    p.add_argument("--out", type=Path, default=Path("output.txt"),
                   help="最終プロンプトの出力先")
    p.add_argument("--workdir", type=Path, default=Path("."),
                   help="中間ファイルの置き場(既定: カレント)")
    p.add_argument("--url", default="http://localhost:8080/v1/chat/completions",
                   help="llama.cpp の OpenAI 互換エンドポイント")
    p.add_argument("--temp", type=float, default=0.1,
                   help="サンプリング温度。構造化抽出は低いほど安定(既定 0.1。0で完全決定的)")
    p.add_argument("--top-p", type=float, default=0.95)
    p.add_argument("--top-k", type=int, default=64)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--from-extracted", dest="from_extracted", type=Path, default=None,
                   help="HTTP段を飛ばし、指定ファイルを抽出結果として使う")
    p.add_argument("--rating", default=None,
                   help="レーティング上書き {safe|sensitive|nsfw|explicit}")
    args = p.parse_args(argv)

    args.workdir.mkdir(parents=True, exist_ok=True)
    extracted = args.workdir / "extracted.txt"
    normalized = args.workdir / "normalized.txt"
    unresolved = args.workdir / "unresolved.log"

    # --- 1) 抽出(llama.cpp / Gemma) ---
    if args.from_extracted is not None:
        print(f"[抽出] HTTP段を飛ばし {args.from_extracted} を使用", file=sys.stderr)
        extracted.write_text(args.from_extracted.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        if args.input is None:
            print("エラー: --input か --from-extracted のどちらかが必要。", file=sys.stderr)
            return 2
        print(f"[抽出] llama.cpp へ送信: {args.url}", file=sys.stderr)
        content = call_llama(
            args.url,
            args.sysfile.read_text(encoding="utf-8"),
            args.input.read_text(encoding="utf-8"),
            args.temp, args.top_p, args.top_k, args.timeout)
        extracted.write_text(content, encoding="utf-8")
    print(f"[抽出] -> {extracted}", file=sys.stderr)

    # --- 2) 正規化(辞書) ---
    print("[正規化] 辞書で別名解決・実在検証", file=sys.stderr)
    run([sys.executable, str(HERE / "normalize.py"),
         "--dict", str(args.dictfile),
         "--in", str(extracted),
         "--out", str(normalized),
         "--log", str(unresolved)])

    # --- 3) 組み立て ---
    print("[組み立て] 最終プロンプトを生成", file=sys.stderr)
    assemble_cmd = [sys.executable, str(HERE / "assemble.py"),
                    "--in", str(normalized), "--out", str(args.out)]
    if args.rating:
        assemble_cmd += ["--rating", args.rating]
    run(assemble_cmd)

    print(f"\n完了: {args.out}", file=sys.stderr)
    print(f"  中間データ: {normalized}", file=sys.stderr)
    print(f"  未解決ログ: {unresolved}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
