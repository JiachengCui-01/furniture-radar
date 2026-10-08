<#
  把运行所需的密钥写入 GitHub 仓库的 Secrets。
  在你自己的电脑上运行，密钥只在本机和 GitHub 之间传输。
  前提：已安装 GitHub CLI（https://cli.github.com）并执行过 gh auth login；在仓库目录下运行：
    powershell -ExecutionPolicy Bypass -File scripts\setup_secrets.ps1
#>
param([string]$Repo = "")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
[Console]::OutputEncoding = [Text.Encoding]::UTF8

if (-not $Repo) { $Repo = (gh repo view --json nameWithOwner -q .nameWithOwner) }
Write-Host "目标仓库：$Repo" -ForegroundColor Cyan

# 读取本地 .env（如果有），已有的值可直接复用
$envValues = @{}
if (Test-Path ".env") {
  Get-Content ".env" -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*)\s*$') { $envValues[$Matches[1]] = $Matches[2].Trim('"').Trim("'") }
  }
}

function Read-Plain([string]$Prompt) {
  $secure = Read-Host -AsSecureString $Prompt
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-Value([string]$Name, [string]$Prompt) {
  if ($envValues[$Name]) {
    $use = Read-Host "$Name 在 .env 中已有值，直接使用？(Y/n)"
    if ($use -ne "n") { return $envValues[$Name] }
  }
  return (Read-Plain $Prompt)
}

function Set-Secret([string]$Name, [string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value)) { Write-Host "  跳过 $Name（未填写）" -ForegroundColor Yellow; return }
  gh secret set $Name --repo $Repo --body $Value | Out-Null
  Write-Host "  已设置 $Name" -ForegroundColor Green
}

Write-Host "`n1/4 卖家精灵密钥（https://open.sellersprite.com → 获取密钥）"
Set-Secret "SELLERSPRITE_SECRET_KEY" (Get-Value "SELLERSPRITE_SECRET_KEY" "粘贴卖家精灵 secret-key")

Write-Host "`n2/4 报告加密主密钥 REPORT_KEY"
$reportKey = $envValues["REPORT_KEY"]
if (-not $reportKey) {
  $reportKey = (python -m radar keygen 2>$null | Select-Object -First 1).Trim()
  Add-Content -Path ".env" -Value "REPORT_KEY=$reportKey" -Encoding UTF8
  Write-Host "  已生成新的 REPORT_KEY 并写入本地 .env。" -ForegroundColor Yellow
  Write-Host "  请务必另外备份到密码管理器：丢失后历史数据和旧报告都无法解密。" -ForegroundColor Yellow
}
Set-Secret "REPORT_KEY" $reportKey

Write-Host "`n3/4 钉钉群机器人（群设置 → 机器人 → 添加自定义机器人 → 安全设置选“加签”）"
Write-Host "  多个群用英文逗号分隔，webhook 和加签密钥按顺序一一对应"
Set-Secret "DINGTALK_WEBHOOK" (Get-Value "DINGTALK_WEBHOOK" "粘贴 webhook 地址")
Set-Secret "DINGTALK_SECRET" (Get-Value "DINGTALK_SECRET" "粘贴加签密钥（SEC 开头）")

Write-Host "`n4/4 可选：DeepSeek API Key（用于生成文字总结，不填则用模板文字）"
Set-Secret "DEEPSEEK_API_KEY" (Get-Value "DEEPSEEK_API_KEY" "粘贴 DeepSeek API Key（直接回车跳过）")

Write-Host "`n完成。接下来：仓库 Settings → Pages → Source 选 GitHub Actions；然后 Actions → 家具爆品雷达 → Run workflow。" -ForegroundColor Cyan
