[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SkipInstaller,
    [string]$NativeRuntimeDir = $env:SOMEIP_AGENT_NATIVE_PACKAGE_DIR
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $ProjectRoot ".build\windows"
$VenvRoot = Join-Path $BuildRoot "venv"
$DistRoot = Join-Path $ProjectRoot "dist"
$OutputRoot = Join-Path $ProjectRoot "packaging\windows\output"

function Write-Step {
    param([int]$Number, [int]$Total, [string]$Message)
    Write-Host ("[步骤 {0}/{1}] {2}" -f $Number, $Total, $Message) -ForegroundColor Cyan
}

function Invoke-Native {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令执行失败（exit=$LASTEXITCODE）：$Executable $($Arguments -join ' ')"
    }
}

try {
    Write-Step 1 7 "检查构建工具与语义版本"
    $SystemPython = (Get-Command python -ErrorAction Stop).Source
    $Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    $Version = (Get-Content (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
    if ($Version -notmatch "^(\d+)\.(\d+)\.(\d+)") {
        throw "无法从版本 $Version 生成 Windows 数字版本"
    }
    $NumericVersion = "$($Matches[1]).$($Matches[2]).$($Matches[3]).0"
    if (-not $NativeRuntimeDir -or -not (Test-Path (Join-Path $NativeRuntimeDir "soa_partner.exe"))) {
        throw "缺少原生 soa_partner.exe；请先执行 build-native.ps1 或指定 -NativeRuntimeDir"
    }
    $NativeRuntimeDir = (Resolve-Path $NativeRuntimeDir).Path
    Invoke-Native $SystemPython @((Join-Path $ProjectRoot "scripts\check_version.py"))

    Write-Step 2 7 "准备干净构建目录"
    if ($Clean) {
        foreach ($Target in @($BuildRoot, (Join-Path $DistRoot "someip-agent"), $OutputRoot)) {
            if (Test-Path $Target) {
                Write-Host "清理受控目录：$Target"
                Remove-Item -LiteralPath $Target -Recurse -Force
            }
        }
    }
    New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

    Write-Step 3 7 "安装前端依赖"
    Invoke-Native $Npm @("--prefix", (Join-Path $ProjectRoot "frontend"), "ci", "--no-audit", "--no-fund")

    Write-Step 4 7 "构建前端静态资源"
    Invoke-Native $Npm @("--prefix", (Join-Path $ProjectRoot "frontend"), "run", "build")

    Write-Step 5 7 "创建隔离 Python 环境并安装打包依赖"
    if (-not (Test-Path $VenvRoot)) {
        Invoke-Native $SystemPython @("-m", "venv", $VenvRoot)
    }
    $BuildPython = Join-Path $VenvRoot "Scripts\python.exe"
    Invoke-Native $BuildPython @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Native $BuildPython @("-m", "pip", "install", "-e", ((Join-Path $ProjectRoot "backend") + "[packaging]"))

    Write-Step 6 7 "运行 PyInstaller"
    Invoke-Native $BuildPython @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", $DistRoot,
        "--workpath", (Join-Path $BuildRoot "pyinstaller"),
        (Join-Path $PSScriptRoot "someip-agent.spec")
    )
    Write-Host "构建独立升级器"
    Invoke-Native $BuildPython @(
        "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--name", "someip-agent-updater",
        "--distpath", (Join-Path $DistRoot "someip-agent"),
        "--workpath", (Join-Path $BuildRoot "updater"),
        "--specpath", $BuildRoot,
        (Join-Path $PSScriptRoot "updater.py")
    )
    $PackagedNative = Join-Path $DistRoot "someip-agent\native"
    New-Item -ItemType Directory -Path $PackagedNative -Force | Out-Null
    Get-ChildItem -LiteralPath $NativeRuntimeDir | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $PackagedNative -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "VERSION") -Destination (Join-Path $DistRoot "someip-agent\VERSION")
    $ZipPackage = Join-Path $OutputRoot "someip-agent-$Version-windows-x64.zip"
    Compress-Archive -Path (Join-Path $DistRoot "someip-agent\*") -DestinationPath $ZipPackage -Force

    if ($SkipInstaller) {
        Write-Host "已跳过 Inno Setup；PyInstaller 输出：$(Join-Path $DistRoot 'someip-agent')" -ForegroundColor Yellow
        exit 0
    }

    Write-Step 7 7 "生成 Inno Setup 安装器与 SHA-256"
    $InnoCandidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $InnoCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) {
        throw "未找到 Inno Setup 6，请安装后重试：https://jrsoftware.org/isdl.php"
    }
    Invoke-Native $Iscc @(
        "/DAppVersion=$Version",
        "/DAppNumericVersion=$NumericVersion",
        "/DProjectRoot=$ProjectRoot",
        (Join-Path $PSScriptRoot "installer.iss")
    )

    $Installer = Get-ChildItem -LiteralPath $OutputRoot -Filter "someip-agent-$Version-windows-x64-setup.exe" |
        Select-Object -First 1
    if (-not $Installer) {
        throw "Inno Setup 已返回成功，但未找到预期安装器"
    }
    $Hash = Get-FileHash -LiteralPath $Installer.FullName -Algorithm SHA256
    $HashLine = "{0}  {1}" -f $Hash.Hash.ToLowerInvariant(), $Installer.Name
    Set-Content -LiteralPath ($Installer.FullName + ".sha256") -Value $HashLine -Encoding Ascii
    Write-Host "构建完成：$($Installer.FullName)" -ForegroundColor Green
    Write-Host "SHA-256：$($Hash.Hash.ToLowerInvariant())" -ForegroundColor Green
}
catch {
    [Console]::Error.WriteLine(("Windows 打包失败：{0}" -f $_.Exception.ToString()))
    if ($_.ScriptStackTrace) {
        [Console]::Error.WriteLine(("PowerShell 调用堆栈：`n{0}" -f $_.ScriptStackTrace))
    }
    throw
}
