<#
  在本机运行一期（读取项目根目录的 .env），报告和加密数据保存在 site\ 目录。
  用法：
    powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1 -Force            # 立即运行
    powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1 -Force -Budget 20 # 限制本期调用次数
    powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1 -Notify           # 满 3 天才运行，完成后推送钉钉
  注意：本地运行的历史数据与 GitHub 上的（site 分支）互相独立。
#>
param([switch]$Force, [int]$Budget = 0, [switch]$Notify)
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONIOENCODING = "utf-8"
$cmd = @("-m", "radar", "run")
if ($Force) { $cmd += "--force" }
if ($Budget -gt 0) { $cmd += @("--budget", "$Budget") }
if ($Notify) { $cmd += "--notify" }
python @cmd
if ($LASTEXITCODE -eq 0) { Write-Host "`n本地解密查看最新报告：python -m radar decrypt site\reports\<日期>.html" }
