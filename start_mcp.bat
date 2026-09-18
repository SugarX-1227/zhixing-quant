@echo off
rem 只读行情 MCP 服务启动脚本。双击即可。
rem token 存在 data\mcp_token.txt（不进 git），首次用
rem   python -c "import secrets;print(secrets.token_urlsafe(32))" > data\mcp_token.txt
rem 生成；两台机器都要用同一份。
cd /d "%~dp0"
set /p MCP_TOKEN=<data\mcp_token.txt
if "%MCP_TOKEN%"=="" (
    echo [错误] data\mcp_token.txt 是空的，先生成 token 再启动。
    pause
    exit /b 1
)
echo 只读行情 MCP 服务启动中（0.0.0.0:8765，本机内网 IP 用 ipconfig 查看）
echo 审计日志：data\mcp_audit.jsonl   关闭服务：直接关掉本窗口
python -m zhixing_quant.mcp_server --http --host 0.0.0.0 --port 8765
pause
