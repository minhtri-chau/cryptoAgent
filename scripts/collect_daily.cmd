@echo off
rem Daily deterministic pipeline (no AI): collect -> quality -> evaluate predictions -> baseline predictions -> report.
rem Run by the "cryptoAgent daily collect" scheduled task; see scripts\register_daily_task.ps1.
rem Safe to run by hand: only complete UTC days are stored and nothing is recorded twice.
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
echo ==== %date% %time% >> data\logs\collect.log
.venv\Scripts\python.exe -m cryptoagent init-db >> data\logs\collect.log 2>&1
.venv\Scripts\python.exe -m cryptoagent collect coinbase >> data\logs\collect.log 2>&1
.venv\Scripts\python.exe -m cryptoagent collect bitstamp >> data\logs\collect.log 2>&1
.venv\Scripts\python.exe -m cryptoagent collect coinmetrics >> data\logs\collect.log 2>&1
.venv\Scripts\python.exe -m cryptoagent quality --limit 0 > data\logs\quality-latest.json 2>> data\logs\collect.log
.venv\Scripts\python.exe -m cryptoagent evaluate >> data\logs\collect.log 2>&1
.venv\Scripts\python.exe -m cryptoagent predict baselines >> data\logs\collect.log 2>&1
.venv\Scripts\python.exe -m cryptoagent report >> data\logs\collect.log 2>&1
