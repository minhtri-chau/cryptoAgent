@echo off
rem OPTIONAL, OFF BY DEFAULT, UNTESTED: an unattended /daily briefing through Claude Code's headless mode.
rem Requires the Claude Code CLI on PATH (`claude --version`) and a logged-in subscription, or a token from
rem `claude setup-token` in CLAUDE_CODE_OAUTH_TOKEN. Without ANTHROPIC_API_KEY set, it uses your subscription and
rem counts towards its usage limits. Run it by hand first and read the output before scheduling it.
rem Tools are limited to the project CLI, reading files, editing under reports/, and the project's subagents;
rem the database refuses user-only strategy decisions from anyone but the user.
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
claude -p "/daily unattended" ^
  --permission-mode dontAsk ^
  --allowedTools "Bash(.venv/Scripts/python -m cryptoagent *),Read,Glob,Grep,Edit(reports/**),Agent" ^
  --max-turns 80 ^
  --output-format json > data\logs\daily-claude-latest.json 2>> data\logs\daily-claude.log
echo ==== %date% %time% exit %errorlevel% >> data\logs\daily-claude.log
