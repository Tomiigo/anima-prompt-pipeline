#!/usr/bin/env bash
# パイプライン実行中だけ llama-server を起動し、終わったら停止して VRAM を解放する。
# 画像生成(Anima)時に llama-server が VRAM を占有しないようにするためのラッパー。
#
# 入力: inputs/ に「変換したい日本語の文章」を書いたテキストファイルを1つ置いてください。
#       ファイル名は何でも構いません。ただし inputs/ に置く .txt は1つだけにしてください。
# 出力: output.txt(最終プロンプト)
set -euo pipefail

# ============ 環境に合わせて編集 ============
LLAMA_DIR="$HOME/llama.cpp"
MODEL_PATH="$LLAMA_DIR/models/gemma-4-12B-it-Q4_K_XL.gguf"
PIPELINE_DIR="$HOME/comfyui_project/anima-prompt-pipeline"
PORT=8080

# llama-server 起動コマンド
SERVER_CMD=( "$LLAMA_DIR/build/bin/llama-server"
  -m "$MODEL_PATH"
  --fit on --fit-target 1536 -c 4096 -ctk q8_0 -ctv q8_0 -b 256
  --flash-attn on -t 8 --jinja
  --chat-template-kwargs '{"enable_thinking":false}'
  --host 127.0.0.1 --port "$PORT" )
# ============ ここから下は触らなくてよい ============

DICT="$PIPELINE_DIR/booru_dict.json"
SYS="$PIPELINE_DIR/system_prompt.txt"
OUT="$PIPELINE_DIR/output.txt"
INPUTS_DIR="$PIPELINE_DIR/inputs"

SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[終了] llama-server を停止して VRAM を解放します (PID $SERVER_PID)"
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# --- 入力ファイルの自動検出(inputs/ の .txt をちょうど1つ) ---
# README.md は除外。サーバ起動前に確認し、無い/複数ならすぐ止める。
mapfile -t INPUT_FILES < <(find "$INPUTS_DIR" -maxdepth 1 -type f -name '*.txt' ! -iname 'readme*' | sort)
if [[ "${#INPUT_FILES[@]}" -eq 0 ]]; then
  echo "[エラー] inputs/ に入力テキスト(.txt)がありません。" >&2
  echo "        変換したい日本語の文章を書いたファイルを inputs/ に1つ置いてください(ファイル名は任意)。" >&2
  exit 1
fi
if [[ "${#INPUT_FILES[@]}" -gt 1 ]]; then
  echo "[エラー] inputs/ に .txt が複数あります。1つだけにしてください:" >&2
  printf '          - %s\n' "${INPUT_FILES[@]}" >&2
  exit 1
fi
INPUT_FILE="${INPUT_FILES[0]}"
echo "[入力] $INPUT_FILE"

echo "[起動] llama-server を起動します"
"${SERVER_CMD[@]}" >/tmp/llama-server.log 2>&1 &
SERVER_PID=$!

echo "[待機] サーバの準備完了(/health)を待ちます…"
ready=0
for _ in $(seq 1 180); do
  if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    ready=1; break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[エラー] llama-server が起動に失敗しました。/tmp/llama-server.log を確認してください。" >&2
    exit 1
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  echo "[エラー] 準備完了の確認がタイムアウトしました。/tmp/llama-server.log を確認してください。" >&2
  exit 1
fi
echo "[準備OK] サーバが応答しました"

echo "[実行] パイプライン"
cd "$PIPELINE_DIR"
python3 run_pipeline.py \
  --input "$INPUT_FILE" \
  --dict "$DICT" \
  --sys "$SYS" \
  --out "$OUT" "$@"

echo "[完了] output.txt を生成しました。サーバを停止して VRAM を解放します。"
# サーバ停止は trap cleanup で自動実行されます
