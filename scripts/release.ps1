<#
.SYNOPSIS
    一键发布一个 Agent：归档 .agent、提交、打 tag（可选推送 + 建 Release）。

.DESCRIPTION
    在仓库根目录执行：
        .\scripts\release.ps1 -Agent music-player
        .\scripts\release.ps1 -Agent music-player -Push

    前提：已经在 Booster Studio 里构建过一次（存在 agents\<名称>\build\*.agent）。

.PARAMETER Agent
    agents/ 下的目录名，例如 music-player、volume-adjustment。

.PARAMETER Push
    同时推送 main 和 tags，并在 gh 可用时创建 GitHub Release。

.PARAMETER Message
    自定义提交信息，默认 "release(<agent>): v<版本>".
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Agent,

    [switch]$Push,

    [string]$Message = ""
)

$ErrorActionPreference = "Stop"

function Fail($msg) {
    Write-Host "✗ $msg" -ForegroundColor Red
    exit 1
}

function Step($msg) {
    Write-Host ""
    Write-Host "▶ $msg" -ForegroundColor Cyan
}

# ---------------------------------------------------------------- 定位仓库
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
Write-Host "仓库根目录: $repoRoot"

$agentDir = Join-Path $repoRoot (Join-Path "agents" $Agent)
if (-not (Test-Path $agentDir)) {
    Fail "找不到 Agent 目录: agents\$Agent"
}

# ---------------------------------------------------------------- 读版本
Step "读取 agent.toml"
$tomlPath = Join-Path $agentDir "agent.toml"
if (-not (Test-Path $tomlPath)) { Fail "缺少 agent.toml" }

$toml = Get-Content $tomlPath -Raw -Encoding UTF8
$idMatch = [regex]::Match($toml, '(?m)^\s*id\s*=\s*"([^"]+)"')
$verMatch = [regex]::Match($toml, '(?m)^\s*version\s*=\s*"([^"]+)"')
if (-not $idMatch.Success -or -not $verMatch.Success) {
    Fail "agent.toml 里解析不到 id 或 version"
}
$agentId = $idMatch.Groups[1].Value
$version = $verMatch.Groups[1].Value
Write-Host "  id      = $agentId"
Write-Host "  version = $version"

# ---------------------------------------------------------------- 找产物
Step "查找构建产物"
$buildDir = Join-Path $agentDir "build"
$artifact = $null
if (Test-Path $buildDir) {
    $artifact = Get-ChildItem $buildDir -Filter "*.agent" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}
if ($null -eq $artifact) {
    Fail "没找到 .agent 产物。请先在 Booster Studio 里构建这个工程。"
}
Write-Host "  产物: $($artifact.Name)  ($([math]::Round($artifact.Length/1024,1)) KB, $($artifact.LastWriteTime))"

# 版本号与产物名必须一致，否则会出现"改了 version 但装的是旧包"
$expectedName = "$agentId-$version.agent"
if ($artifact.Name -ne $expectedName) {
    Fail "产物名与 agent.toml 不一致：期望 $expectedName，实际 $($artifact.Name)。请改好 version 后重新构建。"
}

# ---------------------------------------------------------------- 归档
Step "归档到 agents\$Agent\"
$target = Join-Path $agentDir $expectedName
Copy-Item $artifact.FullName $target -Force
Write-Host "  -> agents\$Agent\$expectedName"

# ---------------------------------------------------------------- 工作区状态
Step "检查工作区"
$status = git status --porcelain
if ([string]::IsNullOrWhiteSpace($status)) {
    Write-Host "  工作区干净，无需提交"
} else {
    Write-Host "  有未提交改动:"
    $status -split "`n" | Where-Object { $_ } | ForEach-Object { Write-Host "    $_" }
}

# ---------------------------------------------------------------- 提交
Step "提交"
git add -A
if ($LASTEXITCODE -ne 0) { Fail "git add 失败" }

$staged = git diff --cached --name-only
if ([string]::IsNullOrWhiteSpace($staged)) {
    Write-Host "  没有新的改动需要提交"
} else {
    if ([string]::IsNullOrWhiteSpace($Message)) {
        $Message = "release($Agent): v$version"
    }
    git commit -m $Message
    if ($LASTEXITCODE -ne 0) { Fail "git commit 失败" }
}

# ---------------------------------------------------------------- 打 tag
Step "打 tag"
$tag = "$Agent-v$version"
$existing = git tag --list $tag
if ($existing) {
    Write-Host "  tag $tag 已存在，跳过"
} else {
    git tag -a $tag -m "$agentId $version"
    if ($LASTEXITCODE -ne 0) { Fail "git tag 失败" }
    Write-Host "  已创建 tag: $tag"
}

# ---------------------------------------------------------------- 推送
if ($Push) {
    Step "推送 main 和 tags"
    git push origin main --follow-tags
    if ($LASTEXITCODE -ne 0) { Fail "git push 失败" }

    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($null -ne $gh) {
        Step "创建 GitHub Release"
        $notes = "Agent: ``$agentId`` 版本 ``$version``.`n`n安装包：``$expectedName``"
        gh release create $tag $target --title "$Agent v$version" --notes $notes
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  Release 已创建（可直接下载安装包）"
        } else {
            Write-Host "  Release 创建失败，可稍后手动执行：" -ForegroundColor Yellow
            Write-Host "  gh release create $tag `"$target`" --title `"$Agent v$version`"" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  未检测到 gh，跳过 Release。可手动在 GitHub 网页上传安装包。" -ForegroundColor Yellow
    }
} else {
    Step "下一步"
    Write-Host "  git push origin main --follow-tags"
    Write-Host "  或重新执行： .\scripts\release.ps1 -Agent $Agent -Push"
}

# ---------------------------------------------------------------- 完成
Write-Host ""
Write-Host "✓ 完成: $Agent v$version  (tag: $tag)" -ForegroundColor Green
Write-Host ""
Write-Host "别忘了：在 CHANGELOG.md 里补一行这个版本的说明。" -ForegroundColor Yellow
