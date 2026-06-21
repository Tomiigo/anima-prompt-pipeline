import sys
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import requests
import uvicorn
from pathlib import Path

# プロジェクトのモジュールをインポート
import config
from pipeline import AnimaPipeline

app = FastAPI(title="Anima Prompt Pipeline Web UI")

# 静的ファイルのパス
WEB_DIR = Path(__file__).resolve().parent / "web"

class GenerateRequest(BaseModel):
    prompt: str
    extra_tags: list[str] = []
    temperature: float | None = None
    max_tokens: int | None = None
    fuzzy_cutoff: int | None = None
    translate_first: bool | None = None

@app.get("/api/status")
def get_status():
    # 辞書のチェック
    dict_exists = config.ALIAS_MAP.exists()
    
    # Gemmaサーバーのチェック
    gemma_online = False
    try:
        # llama-serverのモデル一覧取得を試みる
        # タイムアウトは1.0秒にしてレスポンスを早くする
        r = requests.get(f"{config.CHAT_URL}/v1/models", timeout=1.0)
        if r.status_code == 200:
            gemma_online = True
    except Exception:
        pass
        
    return {
        "dictionary_exists": dict_exists,
        "gemma_online": gemma_online,
        "gemma_url": config.CHAT_URL,
        "alias_map_path": str(config.ALIAS_MAP),
    }

@app.post("/api/generate")
def generate_prompt(req: GenerateRequest):
    if not config.ALIAS_MAP.exists():
        raise HTTPException(
            status_code=400, 
            detail="辞書ファイルが見つかりません。先に辞書を作成してください。"
        )
        
    # パラメータの動的適用
    original_temp = config.GEN_TEMPERATURE
    original_max_tokens = config.GEN_MAX_TOKENS
    original_cutoff = getattr(config, "SNAP_FUZZY_CUTOFF", 90)
    original_translate = config.TRANSLATE_FIRST
    
    try:
        if req.temperature is not None:
            config.GEN_TEMPERATURE = req.temperature
        if req.max_tokens is not None:
            config.GEN_MAX_TOKENS = req.max_tokens
        if req.fuzzy_cutoff is not None:
            config.SNAP_FUZZY_CUTOFF = req.fuzzy_cutoff
        if req.translate_first is not None:
            config.TRANSLATE_FIRST = req.translate_first
            
        pipe = AnimaPipeline()
        res = pipe.run(req.prompt, extra_tags=req.extra_tags)
        return res
        
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        raise HTTPException(
            status_code=503,
            detail=f"Gemma サーバー({config.CHAT_URL})に接続できません。llama-server が起動しているか確認してください。"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"エラーが発生しました: {str(e)}"
        )
    finally:
        # 設定を元に戻す
        config.GEN_TEMPERATURE = original_temp
        config.GEN_MAX_TOKENS = original_max_tokens
        config.SNAP_FUZZY_CUTOFF = original_cutoff
        config.TRANSLATE_FIRST = original_translate

# 静的ファイルのルーティング
# APIの後に定義することで、APIエンドポイントが優先されるようにする
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:
    @app.get("/")
    def index_fallback():
        # もしwebディレクトリがまだ作られていなければ、一時的にメッセージを返す
        return HTMLResponse(
            content="<h1>Anima Pipeline API is running</h1><p>Web assets directory is missing. Please create 'web/' directory.</p>",
            status_code=200
        )

# HTMLResponseのインポートが必要な場合のための安全策
from fastapi.responses import HTMLResponse

if __name__ == "__main__":
    print("Starting Anima Prompt Pipeline Web App on http://127.0.0.1:7865")
    uvicorn.run(app, host="127.0.0.1", port=7865)
