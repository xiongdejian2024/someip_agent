# Linux 发行包与在线升级

本阶段只验收 Linux；Windows 打包/调试按用户要求暂缓，保留手动 CI 入口。
复用现有 PyInstaller 可选依赖，不增加自研打包器或修改 SOME/IP/SD 协议栈。

## 构建与运行

```bash
make native-image
make package-linux
# 已有同一固定版本原生镜像时可传 NATIVE_IMAGE，避免重复编译协议栈。
make linux-update-test LINUX_UPDATE_EVIDENCE=build/linux-update-new-run
make linux-clean-test LINUX_CLEAN_EVIDENCE=build/linux-clean-new-run
```

构建必须在目标 Linux 架构执行。当前基于 Debian 12/glibc，不承诺跨架构、musl 或更旧 glibc。
干净运行测试使用 Debian slim，仅复制发行目录，不安装 Python 或原生 SDK；容器网络为 none。
基础镜像可通过 `LINUX_RUNTIME_BASE` 显式指定。本地 Docker Hub TLS 超时后使用 ECR
镜像 `public.ecr.aws/docker/library/debian:bookworm-slim`，最终来源摘要记录在容器证据中。
干净验收还通过真实HTTP导入现有ARXML黄金夹具与PCAP：核对标量、嵌套结构/数组、截断错误、
原始Payload及真实返回码，并保存原生解码进程的完整日志。宿主仅用标准库发HTTP，运行容器
内不安装Python/编译器/SDK、不挂载产品源码。该夹具不证明用户车型ARXML的运行覆盖。
`packaging/linux/Dockerfile` 复用原生镜像、现有后端和前端，补齐构建用 Python 共享库。
ZIP 位于镜像 `/opt/linux-package/`，可通过临时 `docker create` + `docker cp` 导出；
完整运行目录位于 `/opt/linux-package/release/`。构建目录已存在时拒绝覆盖。

ZIP 解压后直接运行 `./someip-agent`，不要求目标机安装 Python。主程序采用 onedir，
升级器 `someip-agent-updater` 为独立 onefile；发行包保留执行权限，不包含符号链接。
`_internal/native/soa_partner` 与 vsomeip cfg/SD/E2E 插件、libtins/libpcap 依赖一起收集。
`build-info.json` 记录架构、工具版本、原生/主程序/升级器 SHA；`third-party` 保留原生
来源说明、许可和局部补丁。发行合规审计及完整 SBOM 仍是发布门禁。

默认只监听 127.0.0.1。持久数据使用 `$XDG_DATA_HOME/someip-agent/data`，未设置时为
用户 `.local/share/someip-agent/data`，不存入会被原子替换的安装目录。
可用 `SOMEIP_AGENT_DATA_DIR` 显式覆盖，但升级暂存目录不得位于安装目录内。
网卡抓包/真实网络权限仍需单独显式授权；发行包不会自动提权或安装驱动。

## 升级源与按钮

配置 `SOMEIP_AGENT_UPDATE_MANIFEST_URL` 和 `SOMEIP_AGENT_UPDATE_PUBLIC_KEY` 后，设置页
先检查更新，只有新版本清单通过 Ed25519 签名验证才显示可执行的升级按钮。
Linux 只接受完整 ZIP；不允许用 Windows EXE 或覆盖个别文件冒充发行版升级。

清单生成继续使用现有 `scripts/create_update_manifest.py`：

```bash
python scripts/create_update_manifest.py \
  --artifact /明确的发行包路径/release.zip \
  --download-url https://你的受信发布源/release.zip \
  --private-key /发布环境中的Ed25519私钥.pem \
  --output /明确的输出目录/manifest.json
```

私钥只属于受保护的发布环境，不提交、不放入客户端，不使用测试私钥作为正式发布信任根。
正式 HTTPS 最新版地址及信任公钥未配置时，客户端明确无可用更新，不从未签名 GitHub 资产猜测。

### 已选发布渠道：GitHub Releases

用户已选择`https://github.com/xiongdejian2024/someip_agent/releases`。
每个正式版本上传完整Linux ZIP和按架构命名的签名清单；提交源码或上传Actions测试
制品不等于发布正式Release。以下只是拟定格式，当前尚未发布这些附件：

- 最新清单：`https://github.com/xiongdejian2024/someip_agent/releases/latest/download/manifest-linux-x86_64.json`。
- 清单中的ZIP地址：`https://github.com/xiongdejian2024/someip_agent/releases/download/v<version>/someip-agent-<version>-linux-x86_64.zip`。
- aarch64使用独立同名架构清单/ZIP，不共用x86_64更新地址。客户端仍需配置可信公钥。

CI仅在原生/安装包及升级/回滚、干净包验收通过后，从构建镜像导出仓库VERSION
对应的基线ZIP，保存为独立`linux-release-x86_64`制品。不能从
`linux-package-update-evidence`挑选第二版本测试ZIP发布；该证据还含测试信任根。
这一步不创建标签、GitHub Release或签名密钥，正式发布仍须等待生产信任根配置。

GitHub的latest和附件下载可能重定向到版本页/CDN。升级器复用既有HTTPX的重定向和
异步request hook，每跳发送前验证HTTPS且不含登录凭据，最多5跳，保留TLS验证；
不会跟随HTTP降级或无限循环。签名仍绑定清单中的原始版本ZIP地址，不绑定临时CDN
地址；下载仍执行大小限制和最终SHA-256检查。
依据：[GitHub附件链接](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases)、
[HTTPX发送前hook](https://www.python-httpx.org/advanced/event-hooks/)。

正式签名密钥生成及保存到仓库Actions Secrets的授权仍待确认，未创建密钥或Secret。
私钥不进入源码/安装包/聊天；没有正式信任根时不发布未签名清单或使用临时测试密钥。
车型源冲突按用户要求保留不解决，正式发布说明需列明未完成的车型互操作验收。

本轮隔离Linux源码升级回归位于`build/github-release-redirect-linux-source-evidence`：
44项通过、零失败/错误/跳过，包括直连与HTTPS跳转的真实脚本进程升级/回滚。
跨域CDN跳转、HTTP降级、凭据、循环、签名/哈希篡改和大小门禁由HTTPX
MockTransport另外验收；不是公网GitHub下载或新安装包的真实浏览器点击证明。
首次Linux检查未设置源码导入路径，实际导入镜像的旧安装模块，34通过/10失败；
完整堆栈保留于`build/github-release-redirect-linux-evidence`，新证据不覆盖它。
macOS首次本地检查40通过/4失败（健康等待和进程退出超时），JUnit保留于
`build/github-release-redirect-evidence`，不计为当前Linux目标通过。
完整原生后端源码回归另在`build/github-release-redirect-backend-ipv6-evidence`完成：
587项通过、零失败/错误/跳过，3项既有依赖/测试警告。使用既有已验证原生镜像，
显式挂载本轮后端源码，强制原生测试；不是本轮新编译Linux发行包验收。
首轮缺少项目现有IPv6回环sysctl，585通过/2失败，完整日志保留在
`build/github-release-redirect-backend-evidence`；重试启用既有回环配置，没有扩展IPv6功能。

升级器在主程序退出前完成解压、VERSION、必需程序及权限检查；准备失败时原程序保持运行。
准备成功后旧目录改名为 `.previous`，新目录原子换入，启动新程序并核对健康接口版本。
新程序失败时恢复旧目录并验证旧版健康；失败现场保留，回滚健康失败不会报告升级成功。
连续升级会归档上一轮备份，不覆盖既有恢复点。

安装请求返回`installation_id`，页面按`GET /api/v1/updates/install/{installation_id}`
查询外置状态，并与实际健康版本核对。状态文件以同目录原子替换写入；只接受本次ID，
不返回进程PID或备份路径。`rollback_completed`只在旧程序健康检查通过后写入，
回滚健康失败另记`rollback_failed`，不能凭进程已启动、旧版可达或残留记录宣称成功。
准备完成仍保持页面安装按钮忙态；完成才重载，恢复失败则明确显示需人工处理。

独立升级器与重启程序设置 `PYINSTALLER_RESET_ENVIRONMENT=1`，避免继承 onefile 临时目录
生命周期。依据 [PyInstaller 官方重启说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart)。

## 验收口径

`native/tests/run_linux_update.sh` 使用容器回环 HTTPS、临时证书和临时签名密钥，容器
`--network none`；不访问公网更新源、不升级用户当前安装目录。
第二版本 0.1.1 仅在证据目录的源码副本中真实编译，四份源码版本一致；仓库仍保持 0.1.0，
测试版不发布。源脚本基线与真实 PyInstaller 主程序/升级器分别验收，不把前者当发行包。

测试覆盖发行包缺省安装根目录/升级器发现、权限及外置暂存门禁、HTTPS 签名升级、重启版本、旧版备份、网页
随包提供、随包原生发生器启动，以及删除新包 Python 运行库后的真实启动失败与旧版回滚。
JUnit、构建日志、应用日志、安装计划/状态与新旧包摘要均保留。准备阶段拒绝与启动后回滚
分别验证。正式发布源、浏览器真实点击、跨 Linux 发行版和规模长稳不能由这一组 API 测试推导。

## 真实浏览器点击验收

浏览器验收使用独立镜像安装测试防火墙工具，不增加正式发行包依赖。
Docker 必须使用独立 bridge 网络，禁止 `--network host`；仅发布宿主
`127.0.0.1:18765`。脚本在容器自己的网络命名空间拒绝外连，只允许回环通信、
浏览器入站端口和已建立连接的响应，不修改宿主防火墙或车辆网卡。
Docker Desktop 的内部网络在当前环境无法映射本机端口，不能直接用它代替上述方案。

先在无网络容器中构建一次真实第二版本，再使用新证据目录操作浏览器：

```bash
mkdir -p build/browser-fixture-new-run
docker run --rm --init --network none \
  -v "$PWD/build/browser-fixture-new-run:/evidence" \
  someip-agent-linux:package \
  python packaging/linux/prepare_update_fixture.py \
    --output /evidence/fixture --native-binary /workspace/build/native/soa_partner
make linux-browser-image
make linux-browser-update-test \
  LINUX_BROWSER_EVIDENCE=build/browser-success-new-run \
  LINUX_BROWSER_FIXTURE=build/browser-fixture-new-run/fixture/new
# 另一个新目录验收启动失败回滚，不覆盖成功记录。
make linux-browser-update-test BROWSER_UPDATE_CASE=linux-rollback \
  LINUX_BROWSER_EVIDENCE=build/browser-rollback-new-run \
  LINUX_BROWSER_FIXTURE=build/browser-fixture-new-run/fixture/new
```

看到就绪提示后，浏览器访问本机端口，打开“模型与设置”，依次点击“检查更新”和
“升级到最新版本”。必须先看到新版本与签名已验证；不调用安装 API 代替页面点击。
测试完成真实重启/回滚、健康版本、安装状态与备份断言后保持进程运行，供浏览器核对；
确认页面版本并保存截图后，回车清理本次临时进程。
旧证据目录拒绝重用，发行包夹具只读挂载，清理只归还本次写入证据的所有权。

本地成功点击的独立证据位于 `build/browser-update-917bbff-retry-evidence`：
页面从 0.1.0 自动重载为 0.1.1，健康接口同步，升级状态 complete，旧版备份 0.1.0。
JUnit 一项通过；当时旧清理脚本因试图 chown 只读夹具而退出 1，已保留异常并修正脚本，
不能将功能断言通过误报为该次整个命令退出成功。
首次未点击时的 180 秒等待超时保留在 `build/browser-update-917bbff-evidence`，不覆盖。
这些仅证明临时信任源的浏览器链路，不证明正式最新版发布或正式信任源已配置。

`build/browser-update-rollback-917bbff-evidence` 单独验收删除新包运行库后的真实启动失败：
升级状态 failed、失败新目录 VERSION 为 0.1.1，恢复运行目录与健康版本均为 0.1.0。
当时页面仍在线并保留旧版本，90 秒后只有通用重启超时提示。
JUnit 一项通过，修正清理脚本后命令退出 0。发行包非交互升级回归 19 项通过（1 warning），
8 项门禁/清理替身回归在 macOS 与 Linux 分别通过；后者不承担防火墙或真实升级证明。

新增安装状态后的真实回滚证据为`build/rollback-status-browser-retry-evidence`：
页面实际点击受信测试更新，缺少运行库的新程序退出255，旧版恢复健康后页面明确显示
“升级失败，已回滚至v0.1.0”及失败原因，随后页面服务正常；JUnit一项通过、命令退出0。
首轮API客户端40秒先于实测约41.7秒的准备完成而超时，以及首轮浏览器总等待180秒耗尽，
分别保留在`build/rollback-status-linux-evidence`与`build/rollback-status-browser-evidence`，
不能计作整轮成功。API测试请求预算对齐页面既有180秒，浏览器总测试窗口另包含页面操作时间；
未修改产品下载、升级准备、退出或重启健康预算。
成功重载独立证据`build/rollback-status-browser-success-evidence`实际页面从0.1.0重载为0.1.1，
状态complete、健康新版本及旧版备份断言通过，JUnit一项通过、命令退出0。
串行完整复验`build/rollback-status-linux-retry-evidence`34项通过、零失败/错误/跳过，1 warning；
新旧发行包及本次完整堆栈各自保留，不覆盖首次失败，也不发布测试信任根。
