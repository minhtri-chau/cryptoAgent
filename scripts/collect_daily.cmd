@echo off
rem Daily deterministic pipeline (no AI): collect -> quality checks. Run by the "cryptoAgent daily collect" scheduled task;
rem see scripts\register_daily_task.ps1. Safe to run by hand: only complete UTC days are stored and nothing is duplicated.
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
echo ==== %date% %time% >> data\logs\collect.log
.venv\Scripts\python.exe -m cryptoagent init-db >> data\logs\collect.log 2>&1
.venv\Scripts\python.exe -m cryptoagent collect coinbase >> data\logs\collect.log 2>&1
.venv\Scripts\python.exe -m cryptoagent collect bitstamp >> data\logs\collect.log 2>&1
.venv\Scripts\python.exe -m cryptoagent collect coinmetrics >> data\logs\collect.log 2>&1
.venv\Scripts\python.exe -m cryptoagent quality --limit 0 >> data\logs\collect.log 2>&1
