# Windows 打包与发布

## 1. 工具链

- Windows 10/11 x64；
- PowerShell 7 (`pwsh`)；CI 不使用按 ANSI 读取无 BOM 中文脚本的 Windows PowerShell 5.1；
- Python 3.10+（推荐 3.12 x64）；
- Node.js 20+（推荐 22 LTS）；
- [PyInstaller](https://pyinstaller.org/) 6.x；
- [Inno Setup](https://jrsoftware.org/isinfo.php) 6.x；
- MSVC x64、CMake、Git 与已有 vcpkg；原生 vsomeip 固定 3.5.10，Boost/JSON 由 vcpkg 提供；
- 实时抓包还需获授权的 Npcap SDK、配置 `Packet_ROOT` 的独立 vcpkg triplet，及目标机驱动/权限；
- 生产发布还需要企业代码签名证书及安全的签名服务。

PyInstaller 不是交叉编译器，Windows 制品必须在 Windows 或等价的受控 Windows 构建环境中
生成。不要从 macOS/Linux 产物改名伪装为 Windows 包。

## 2. 一键构建

在 PowerShell 7 的仓库根目录执行：

```powershell
.\packaging\windows\build-native.ps1 -VcpkgRoot C:\vcpkg -CaptureTriplet x64-windows-npcap -OverlayTriplets C:\approved-triplets
.\packaging\windows\build.ps1 -Clean -NativeRuntimeDir .\.build\native-windows\runtime
```

`C:\approved-triplets\x64-windows-npcap.cmake` 必须使用 x64、动态 CRT/库、Windows 系统，
并通过 `VCPKG_CMAKE_CONFIGURE_OPTIONS` 给 libpcap 传入 `-DPacket_ROOT=已授权的SDK目录`。
构建脚本不会生成/安装 SDK、驱动或下载未经确认的 Npcap 制品。可使用环境变量
`SOMEIP_AGENT_VCPKG_CAPTURE_TRIPLET` 和 `VCPKG_OVERLAY_TRIPLETS` 提供相同配置。
vcpkg libpcap 的标准 Windows 路径可能设置 `PCAP_TYPE=null`；仅编译成功不能证明能抓包。
依据为 [vcpkg 官方 libpcap port](https://github.com/microsoft/vcpkg/blob/master/ports/libpcap/portfile.cmake)
的 Windows `Packet_ROOT` 分支，构建前仍应核对实际锁定的 port 版本。
脚本因此拒绝缺少独立抓包 triplet 的完整发行构建。CI 发布环境也必须明确配置这两个值，
当前仓库尚未验证此 Windows 路径。目标机 `Packet.dll` 的解析、网卡枚举与实际双向捕获仍需实机验收。

CI 使用 `pwsh` 并在构建前解析所有 Windows `.ps1` 脚本，避免将 UTF-8 中文误读成旧 ANSI
代码页。该风险见 [Microsoft PowerShell 字符编码说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding)。
脚本语法通过不代表 SDK、原生编译、驱动或安装升级已经成功。

原生构建器先由 vcpkg 准备 Boost、JSON 和真实捕获后端的 libpcap，再从固定提交构建
libtins 4.6 并核验选项补丁与完整源文件哈希，最后构建 vsomeip 和桥接二进制。
不再使用未固定版本的 vcpkg libtins；详见 [原生依赖来源](native-dependencies.md)。
已有 libtins 源码缓存只接受干净源码或恰好应用本仓库补丁的版本，不隐式 reset 用户修改。

脚本按顺序执行：

1. 校验四处版本均符合 SemVer 且一致；
2. 安装前端依赖并生成 `frontend/dist`；
3. 创建隔离构建虚拟环境并安装后端与 PyInstaller；
4. 用 `someip-agent.spec` 生成 onedir 应用；
5. 用 Inno Setup 生成 per-user x64 安装器；
6. 输出安装器的 SHA-256 文件。

日志会显示每个关键步骤。脚本捕获异常时输出异常文本与 PowerShell 调用堆栈，并以非零状态
退出，不会静默忽略失败。

原生运行目录缺少 `soa_partner.exe` 时禁止生成缺底层的安装包。构建独立 onefile 升级器，并
将原生二进制/DLL、根 VERSION 与 ZIP 完整发行包一起输出。当前改造尚未在 Windows 实机
验收，脚本存在不代表 DLL、安装器和升级回滚已经验证。

只验证 PyInstaller 内容而暂不生成安装器：

```powershell
.\packaging\windows\build.ps1 -SkipInstaller
```

输出目录：

```text
dist/someip-agent/                 # PyInstaller onedir
packaging/windows/output/          # 安装器与 SHA-256
```

## 3. 打包布局

```text
someip-agent/
├── someip-agent.exe
├── someip-agent-updater.exe
├── VERSION
├── native/                        # soa_partner.exe 与原生 DLL
└── _internal/
    ├── web/                       # Vite 静态资源
    ├── VERSION
    ├── .env.example
    └── Python/第三方运行时文件
```

后端定位资源时必须兼容源码模式与 PyInstaller 的 `sys._MEIPASS`，不得假定当前工作目录。用户
数据、工程、密钥和日志必须写入用户数据目录，不得写入 `_internal`。

## 4. 版本发布

发版前同时修改以下四处，然后运行校验：

- `VERSION`
- `backend/pyproject.toml`
- `backend/src/someip_agent/version.py`
- `frontend/package.json`

```powershell
python .\scripts\check_version.py --tag v0.2.0
```

正式标签使用 `vMAJOR.MINOR.PATCH`。版本语义：

- MAJOR：工程/API 或插件 ABI 不兼容；
- MINOR：向后兼容功能；
- PATCH：向后兼容修复；
- 预发布：`-alpha.N`、`-beta.N`、`-rc.N`；
- 构建元数据可用于内部追踪，但安装器升级顺序只依赖核心/预发布版本策略。

## 5. 签名顺序

推荐顺序：

1. 在隔离 runner 中构建；
2. 对 `someip-agent.exe` 及需要的 DLL 做 Authenticode 签名和时间戳；
3. 构建 Inno Setup 安装器；
4. 对安装器签名和时间戳；
5. 计算最终安装器 SHA-256；
6. 生成更新清单，对规范化清单做 Ed25519 离线/托管签名；
7. 上传制品后从下载地址回读，复验签名、哈希、大小和安装冒烟。

签名私钥不得作为普通 GitHub Actions Secret 注入可执行任意 PR 代码的任务。优先使用 HSM、
云签名服务、OIDC 短期授权和受保护发布环境。

生成与当前后端验证逻辑完全一致的签名清单：

```powershell
python .\scripts\create_update_manifest.py `
  --artifact .\packaging\windows\output\someip-agent-0.2.0-windows-x64-setup.exe `
  --download-url https://updates.example.com/someip-agent-0.2.0-windows-x64-setup.exe `
  --private-key D:\secure\update-private.pem `
  --output .\packaging\windows\output\update-manifest.json
```

可用 OpenSSL 创建密钥对（生产环境优先使用 HSM/托管签名服务）：

```powershell
openssl genpkey -algorithm Ed25519 -out update-private.pem
openssl pkey -in update-private.pem -pubout -out update-public.pem
```

脚本只把 Base64 原始公钥打印到发布日志，不输出私钥内容。签名原文为
`version + "\n" + sha256 + "\n" + download_url`，与后端 `UpdateService` 保持一致。发布时将公钥
放入受控客户端配置，将私钥留在发布环境。

## 6. 安装与升级策略

- 安装器使用稳定 `AppId`，同一产品版本可原位升级；
- 默认 per-user 安装，不自动申请管理员权限，也不私自添加防火墙规则；
- 默认只监听 `127.0.0.1`；远程访问必须由管理员显式配置 TLS/认证；
- 卸载程序不主动删除用户工程和凭据；清理数据应提供单独、可确认的操作；
- Npcap、硬件驱动和厂商 SDK 单独安装并检测版本，主安装器不捆绑未知许可驱动；
- 失败升级保留上一版本，数据库 schema 迁移前必须备份且支持回滚。

## 7. 发布验收清单

- [ ] 干净 Windows 10 与 11 x64 安装、启动、升级、卸载通过；
- [ ] 无 Python/Node 开发环境的机器可以运行；
- [ ] ARXML/PCAP 导入、监控页面、模型配置冒烟通过；
- [ ] 安装器、EXE、DLL 签名有效且有可信时间戳；
- [ ] 安装包 SHA-256 与签名更新清单一致；
- [ ] SBOM、`THIRD_PARTY_NOTICES`、许可证文本完整；
- [ ] Defender/企业 EDR 扫描，无未解释告警；
- [ ] API Key 不在文件、注册表明文、日志或安装包中；
- [ ] 断网、损坏下载、错误签名、磁盘不足和进程占用场景可恢复；
- [ ] 旧版本数据备份与回滚演练通过。
