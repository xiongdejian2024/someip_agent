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

升级器在主程序退出前完成解压、VERSION、必需程序及权限检查；准备失败时原程序保持运行。
准备成功后旧目录改名为 `.previous`，新目录原子换入，启动新程序并核对健康接口版本。
新程序失败时恢复旧目录并验证旧版健康；失败现场保留，回滚健康失败不会报告升级成功。
连续升级会归档上一轮备份，不覆盖既有恢复点。

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
