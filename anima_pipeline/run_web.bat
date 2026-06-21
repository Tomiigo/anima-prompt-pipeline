@echo off
echo ===================================================
echo  Anima Prompt Pipeline Web App 起動スクリプト
echo ===================================================
echo.
echo 仮想環境 (../winvenv) の依存関係を確認します...
..\winvenv\Scripts\pip.exe install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [!] 依存関係のインストール/確認に失敗しました。
    pause
    exit /b
)

echo.
echo Web アプリサーバーを起動します...
echo ブラウザが自動的に開かない場合は http://127.0.0.1:7865 を開いてください。
echo.

start http://127.0.0.1:7865
..\winvenv\Scripts\python.exe app_web.py

pause
