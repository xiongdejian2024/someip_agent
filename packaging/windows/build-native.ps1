[CmdletBinding()]
param(
    [string]$VcpkgRoot = $env:VCPKG_INSTALLATION_ROOT,
    [string]$CaptureTriplet = $env:SOMEIP_AGENT_VCPKG_CAPTURE_TRIPLET,
    [string]$OverlayTriplets = $env:VCPKG_OVERLAY_TRIPLETS
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$NativeRoot = Join-Path $ProjectRoot ".build\native-windows"
$SourceRoot = Join-Path $NativeRoot "vsomeip"
$TinsSourceRoot = Join-Path $NativeRoot "libtins"
$InstallRoot = Join-Path $NativeRoot "install"
$RuntimeRoot = Join-Path $NativeRoot "runtime"

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "原生构建命令失败（exit=$LASTEXITCODE）：$Executable" }
}

try {
    if (-not $VcpkgRoot -or -not (Test-Path (Join-Path $VcpkgRoot "vcpkg.exe"))) {
        throw "请通过 -VcpkgRoot 指定已有 vcpkg 目录；构建器不会隐式安装系统工具"
    }
    if (-not $CaptureTriplet -or -not $OverlayTriplets) {
        throw "实时抓包构建必须明确提供 -CaptureTriplet 与 -OverlayTriplets（配置 Npcap SDK 的 Packet_ROOT）；默认 libpcap 可能只有 null 捕获后端，禁止生成缺抓包能力的完整发行包"
    }
    if ($CaptureTriplet -notmatch '^x64-windows-[a-z0-9-]+$') {
        throw "请提供独立命名的 x64-windows 抓包 triplet，避免混用默认 null 后端缓存"
    }
    $TripletFile = Join-Path $OverlayTriplets ($CaptureTriplet + ".cmake")
    if (-not (Test-Path -LiteralPath $TripletFile)) { throw "抓包 triplet 文件不存在：$TripletFile" }
    $TripletSource = Get-Content -LiteralPath $TripletFile -Raw
    if ($TripletSource -notmatch 'VCPKG_CMAKE_CONFIGURE_OPTIONS' -or $TripletSource -notmatch 'Packet_ROOT') {
        throw "抓包 triplet 必须通过 VCPKG_CMAKE_CONFIGURE_OPTIONS 指定已授权 Npcap SDK 的 Packet_ROOT"
    }
    New-Item -ItemType Directory -Path $NativeRoot, $InstallRoot, $RuntimeRoot -Force | Out-Null
    Write-Host "[原生步骤 1/4] 准备 Boost、JSON 与含实际捕获后端的 libpcap"
    Invoke-Checked (Join-Path $VcpkgRoot "vcpkg.exe") @("install", "boost-system", "boost-thread", "boost-filesystem", "nlohmann-json", "libpcap", "--triplet", $CaptureTriplet, "--overlay-triplets=$OverlayTriplets")
    $Toolchain = Join-Path $VcpkgRoot "scripts\buildsystems\vcpkg.cmake"
    Write-Host "[原生步骤 2/4] 构建固定提交的 libtins 4.6 与 RFC 791 选项补丁"
    if (-not (Test-Path $TinsSourceRoot)) {
        Invoke-Checked "git" @("-c", "core.autocrlf=false", "clone", "--depth", "1", "--branch", "v4.6", "https://github.com/mfontanini/libtins.git", $TinsSourceRoot)
    }
    $TinsRevision = (& git -C $TinsSourceRoot rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $TinsRevision -ne "2d2f7012d9f3a16d684a55ba39f1215b6aef5429") {
        throw "libtins 源码提交与固定版本不一致"
    }
    $TinsPatch = Join-Path $ProjectRoot "native\patches\libtins-ipv4-options.patch"
    # 缓存只接受原始源码或本项目已应用补丁；不 reset 或覆盖用户修改。
    $TinsChanges = @(& git -C $TinsSourceRoot status --porcelain)
    if ($LASTEXITCODE -ne 0) { throw "libtins 源码状态核验失败" }
    if ($TinsChanges.Count -eq 0) {
        Invoke-Checked "git" @("-C", $TinsSourceRoot, "apply", "--check", $TinsPatch)
        Invoke-Checked "git" @("-C", $TinsSourceRoot, "apply", $TinsPatch)
    }
    else {
        if ($TinsChanges.Count -ne 1 -or $TinsChanges[0] -ne " M src/ip.cpp") {
            throw "libtins 缓存包含其他修改，拒绝隐式替换"
        }
        Invoke-Checked "git" @("-C", $TinsSourceRoot, "apply", "--reverse", "--check", $TinsPatch)
    }
    $TinsPatchedHash = (Get-FileHash -LiteralPath (Join-Path $TinsSourceRoot "src\ip.cpp") -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($TinsPatchedHash -ne "fe3b9717eb8b53e5fef9736854cf5fb18f055139f2a812b4e7e24ffd0e26f486") {
        throw "libtins 补丁后源码哈希不一致，不能复用有额外修改的缓存"
    }
    $TinsBuild = Join-Path $NativeRoot "libtins-build"
    Invoke-Checked "cmake" @("-S", $TinsSourceRoot, "-B", $TinsBuild, "-A", "x64",
        "-DCMAKE_TOOLCHAIN_FILE=$Toolchain", "-DVCPKG_TARGET_TRIPLET=$CaptureTriplet", "-DVCPKG_OVERLAY_TRIPLETS=$OverlayTriplets", "-DCMAKE_INSTALL_PREFIX=$InstallRoot",
        "-DLIBTINS_BUILD_EXAMPLES=OFF", "-DLIBTINS_BUILD_TESTS=OFF", "-DLIBTINS_ENABLE_DOT11=OFF", "-DLIBTINS_ENABLE_TCPIP=ON", "-DLIBTINS_ENABLE_PCAP=ON")
    Invoke-Checked "cmake" @("--build", $TinsBuild, "--config", "Release", "--target", "install", "--parallel", "2")
    Write-Host "[原生步骤 3/4] 构建固定提交的 vsomeip 3.5.10"
    if (-not (Test-Path $SourceRoot)) {
        Invoke-Checked "git" @("clone", "--depth", "1", "--branch", "3.5.10", "https://github.com/COVESA/vsomeip.git", $SourceRoot)
    }
    $Revision = (& git -C $SourceRoot rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $Revision -ne "c4e0db329da9b63f511f3c2456c040582daf9305") {
        throw "vsomeip 源码提交与固定版本不一致"
    }
    Invoke-Checked "cmake" @("-S", $SourceRoot, "-B", (Join-Path $NativeRoot "vsomeip-build"),
        "-A", "x64", "-DCMAKE_TOOLCHAIN_FILE=$Toolchain", "-DVCPKG_TARGET_TRIPLET=$CaptureTriplet", "-DVCPKG_OVERLAY_TRIPLETS=$OverlayTriplets", "-DCMAKE_INSTALL_PREFIX=$InstallRoot",
        "-DENABLE_SIGNAL_HANDLING=ON", "-DDISABLE_DLT=ON", "-DBUILD_TESTING=OFF")
    Invoke-Checked "cmake" @("--build", (Join-Path $NativeRoot "vsomeip-build"), "--config", "Release", "--target", "install", "--parallel", "2")
    Write-Host "[原生步骤 4/4] 构建桥接二进制并收集运行依赖"
    $BridgeBuild = Join-Path $NativeRoot "bridge-build"
    Invoke-Checked "cmake" @("-S", (Join-Path $ProjectRoot "native"), "-B", $BridgeBuild,
        "-A", "x64", "-DCMAKE_TOOLCHAIN_FILE=$Toolchain", "-DVCPKG_TARGET_TRIPLET=$CaptureTriplet", "-DVCPKG_OVERLAY_TRIPLETS=$OverlayTriplets", "-DCMAKE_PREFIX_PATH=$InstallRoot", "-DVSOMEIP_SOURCE_DIR=$SourceRoot")
    Invoke-Checked "cmake" @("--build", $BridgeBuild, "--config", "Release", "--parallel", "2")
    Invoke-Checked "ctest" @("--test-dir", $BridgeBuild, "-C", "Release", "--output-on-failure")
    Copy-Item -LiteralPath (Join-Path $BridgeBuild "Release\soa_partner.exe") -Destination $RuntimeRoot
    foreach ($Directory in @($InstallRoot, (Join-Path $VcpkgRoot "installed\$CaptureTriplet\bin"))) {
        Get-ChildItem -LiteralPath $Directory -Filter "*.dll" -Recurse | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $RuntimeRoot -Force
        }
    }
    Copy-Item -LiteralPath (Join-Path $SourceRoot "LICENSE") -Destination (Join-Path $RuntimeRoot "vsomeip-LICENSE")
    Copy-Item -LiteralPath (Join-Path $TinsSourceRoot "LICENSE") -Destination (Join-Path $RuntimeRoot "libtins-copyright")
    Copy-Item -LiteralPath $TinsPatch -Destination (Join-Path $RuntimeRoot "libtins-ipv4-options.patch")
    foreach ($Library in @("libpcap")) {
        Copy-Item -LiteralPath (Join-Path $VcpkgRoot "installed\$CaptureTriplet\share\$Library\copyright") -Destination (Join-Path $RuntimeRoot ($Library + "-copyright"))
    }
    Write-Warning "编译不代表 Windows 网卡抓包已验收。目标机仍须有获授权的 Npcap 驱动、可解析的 Packet.dll 路径及抓包权限；此脚本不安装驱动或复制 SDK 的运行 DLL"
    $env:SOMEIP_AGENT_NATIVE_PACKAGE_DIR = $RuntimeRoot
    if ($env:GITHUB_ENV) { "SOMEIP_AGENT_NATIVE_PACKAGE_DIR=$RuntimeRoot" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8 }
    Write-Host "原生运行目录已生成：$RuntimeRoot"
}
catch {
    [Console]::Error.WriteLine($_.Exception.ToString())
    [Console]::Error.WriteLine($_.ScriptStackTrace)
    throw
}
