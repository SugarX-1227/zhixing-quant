@echo off
rem Starts the read-only market-data MCP server. Double-click to run.
rem The token lives in data\mcp_token.txt (not in git). Generate it with:
rem   python -c "import secrets;print(secrets.token_urlsafe(32))" > data\mcp_token.txt
rem Both machines must use the same token.
rem
rem NOTE: keep this file pure ASCII + CRLF. cmd.exe reads .bat with the OEM
rem code page (936 here), so UTF-8 Chinese comments can swallow the line
rem breaks and merge the next command into a rem comment.
cd /d "%~dp0"

netstat -an | findstr /c:":8765 " | findstr /i "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] Port 8765 is already in use - the server is probably running.
    echo         Close that server window first if you want to restart it.
    pause
    exit /b 1
)

set /p MCP_TOKEN=<data\mcp_token.txt
if "%MCP_TOKEN%"=="" (
    echo [ERROR] data\mcp_token.txt is empty or missing. Generate a token first.
    pause
    exit /b 1
)
echo Starting read-only MCP server on 0.0.0.0:8765
echo From another machine use this PC's LAN IP (see ipconfig).
echo Audit log: data\mcp_audit.jsonl    Stop: close this window
python -m zhixing_quant.mcp_server --http --host 0.0.0.0 --port 8765
pause
