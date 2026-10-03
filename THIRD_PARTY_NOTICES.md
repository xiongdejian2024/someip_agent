# 第三方软件通知

SOME/IP Agent 使用下列第三方开源软件。此清单记录仓库声明的直接运行/构建依赖，以及会被
Windows 安装包使用或携带的关键传递依赖；版权归各自权利人所有。

本文件不复制完整许可证文本，也不替代许可证原文。正式发布必须从最终 PyInstaller/npm 制品
生成 SBOM 和完整许可证包，并以扫描结果更新本文件；不能仅凭开发环境的依赖解析结果发版。

## 后端运行时

新增原生运行依赖：vsomeip 3.5.10（固定源码提交
`c4e0db329da9b63f511f3c2456c040582daf9305`，MPL-2.0）、Boost（BSL-1.0）和
nlohmann/json（MIT）。发行包需从实际构建输入收集对应完整许可证、版权与源码归档，
不能把这条摘要视为已经完成分发合规门禁。

网卡捕获使用 libtins 4.6（BSD-2-Clause；固定提交
`2d2f7012d9f3a16d684a55ba39f1215b6aef5429`），应用本仓库的 IPv4 选项局部补丁。
Linux/Windows 构建统一核验源码提交与补丁后源文件哈希；许可证及补丁随原生制品收集，
详见 [原生依赖来源](docs/native-dependencies.md)。libpcap 仍使用 Linux Debian 1.10.3
或 Windows vcpkg 的实际获授权抓包配置并归档 copyright。Npcap SDK/驱动是独立依赖，
必须另行确认使用/分发许可，不隐式捆绑；Windows 构建与实际捕获尚未验收。

| 组件 | 仓库声明范围 | 许可证 | 项目/许可证链接 |
|---|---:|---|---|
| FastAPI | `>=0.115,<1` | MIT | [项目](https://github.com/fastapi/fastapi) / [LICENSE](https://github.com/fastapi/fastapi/blob/master/LICENSE) |
| Uvicorn | `>=0.30,<1` | BSD-3-Clause | [项目](https://github.com/encode/uvicorn) / [LICENSE](https://github.com/encode/uvicorn/blob/master/LICENSE.md) |
| dpkt | `>=1.9.8,<2` | BSD-3-Clause | [项目](https://github.com/kbandla/dpkt) / [LICENSE](https://github.com/kbandla/dpkt/blob/master/LICENSE) |
| lxml | `>=5,<7` | BSD-3-Clause | [项目](https://github.com/lxml/lxml) / [LICENSE](https://github.com/lxml/lxml/blob/master/LICENSE.txt) |
| HTTPX | `>=0.27,<1` | BSD-3-Clause | [项目](https://github.com/encode/httpx) / [LICENSE](https://github.com/encode/httpx/blob/master/LICENSE.md) |
| Pydantic | `>=2.9,<3` | MIT | [项目](https://github.com/pydantic/pydantic) / [LICENSE](https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| pydantic-settings | `>=2.5,<3` | MIT | [项目](https://github.com/pydantic/pydantic-settings) / [LICENSE](https://github.com/pydantic/pydantic-settings/blob/main/LICENSE) |
| python-multipart | `>=0.0.12,<1` | Apache-2.0 | [项目](https://github.com/Kludex/python-multipart) / [LICENSE](https://github.com/Kludex/python-multipart/blob/master/LICENSE) |
| cryptography | `>=43,<47` | Apache-2.0 OR BSD-3-Clause | [项目](https://github.com/pyca/cryptography) / [LICENSE](https://github.com/pyca/cryptography/blob/main/LICENSE) |
| keyring | `>=25,<27` | MIT | [项目](https://github.com/jaraco/keyring) / [LICENSE](https://github.com/jaraco/keyring/blob/main/LICENSE) |

Windows/PyInstaller 制品通常还会携带由上述组件解析出的传递依赖。需要特别保留通知的关键项
包括：

| 组件 | 作用 | 许可证 | 项目/许可证链接 |
|---|---|---|---|
| Starlette | FastAPI ASGI 基础 | BSD-3-Clause | [项目](https://github.com/Kludex/starlette) / [LICENSE](https://github.com/Kludex/starlette/blob/master/LICENSE.md) |
| AnyIO | 异步兼容层 | MIT | [项目](https://github.com/agronholm/anyio) / [LICENSE](https://github.com/agronholm/anyio/blob/master/LICENSE) |
| HTTP Core | HTTPX 传输层 | BSD-3-Clause | [项目](https://github.com/encode/httpcore) / [LICENSE](https://github.com/encode/httpcore/blob/master/LICENSE.md) |
| certifi | CA 证书集合 | MPL-2.0 | [项目](https://github.com/certifi/python-certifi) / [LICENSE](https://github.com/certifi/python-certifi/blob/master/LICENSE) |
| cffi / pycparser | cryptography 的 Python/C 接口链 | MIT-0 / BSD-3-Clause | [cffi](https://github.com/python-cffi/cffi) / [pycparser](https://github.com/eliben/pycparser) |
| python-dotenv | pydantic-settings 环境文件支持 | BSD-3-Clause | [项目](https://github.com/theskumar/python-dotenv) / [LICENSE](https://github.com/theskumar/python-dotenv/blob/main/LICENSE) |
| websockets | Uvicorn WebSocket 实现 | BSD-3-Clause | [项目](https://github.com/python-websockets/websockets) / [LICENSE](https://github.com/python-websockets/websockets/blob/main/LICENSE) |
| watchfiles | Uvicorn 开发热更新 | MIT | [项目](https://github.com/samuelcolvin/watchfiles) / [LICENSE](https://github.com/samuelcolvin/watchfiles/blob/main/LICENSE) |

传递依赖会随操作系统、Python 版本、extra 与解析时间变化，以上表格不是穷举清单。

## 前端运行时

| 组件 | 仓库声明范围 | 许可证 | 项目/许可证链接 |
|---|---:|---|---|
| React | `^18.3.1` | MIT | [项目](https://github.com/facebook/react) / [LICENSE](https://github.com/facebook/react/blob/main/LICENSE) |
| React DOM | `^18.3.1` | MIT | [项目](https://github.com/facebook/react) / [LICENSE](https://github.com/facebook/react/blob/main/LICENSE) |
| react-markdown | `^10.1.0` | MIT | [项目](https://github.com/remarkjs/react-markdown) / [LICENSE](https://github.com/remarkjs/react-markdown/blob/main/license) |
| remark-gfm | `^4.0.1` | MIT | [项目](https://github.com/remarkjs/remark-gfm) / [LICENSE](https://github.com/remarkjs/remark-gfm/blob/main/license) |
| Apache ECharts | `^5.6.0` | Apache-2.0 | [项目](https://github.com/apache/echarts) / [LICENSE](https://github.com/apache/echarts/blob/master/LICENSE) / [NOTICE](https://github.com/apache/echarts/blob/master/NOTICE.txt) |
| lossless-json | `4.3.1` | MIT | [项目](https://github.com/josdejong/lossless-json) / [LICENSE](https://github.com/josdejong/lossless-json/blob/main/LICENSE.md) |
| zrender | ECharts 传递依赖 | BSD-3-Clause | [项目](https://github.com/ecomfe/zrender) / [LICENSE](https://github.com/ecomfe/zrender/blob/master/LICENSE.txt) |
| tslib | ECharts 传递依赖 | 0BSD | [项目](https://github.com/microsoft/tslib) / [LICENSE](https://github.com/microsoft/tslib/blob/main/LICENSE.txt) |
| scheduler | React DOM 传递依赖 | MIT | [项目](https://github.com/facebook/react) / [LICENSE](https://github.com/facebook/react/blob/main/LICENSE) |

## 构建、测试与类型定义

这些组件通常不作为应用业务代码直接运行，但参与构建、测试或安装包生成。

| 组件 | 仓库声明范围 | 许可证 | 项目/许可证链接 |
|---|---:|---|---|
| Hatchling | `>=1.27` | MIT | [项目](https://github.com/pypa/hatch) / [LICENSE](https://github.com/pypa/hatch/blob/master/LICENSE.txt) |
| PyInstaller | `>=6.11,<7` | GPL-2.0-or-later，带 Bootloader Exception | [项目](https://github.com/pyinstaller/pyinstaller) / [COPYING](https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt) |
| pytest | `>=8.3,<10` | MIT | [项目](https://github.com/pytest-dev/pytest) / [LICENSE](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |
| pytest-asyncio | `>=0.24,<2` | Apache-2.0 | [项目](https://github.com/pytest-dev/pytest-asyncio) / [LICENSE](https://github.com/pytest-dev/pytest-asyncio/blob/master/LICENSE) |
| pytest-cov / coverage.py | `>=5,<8` | MIT / Apache-2.0 | [pytest-cov](https://github.com/pytest-dev/pytest-cov) / [coverage.py](https://github.com/nedbat/coveragepy) |
| Ruff | `>=0.7,<1` | MIT | [项目](https://github.com/astral-sh/ruff) / [LICENSE](https://github.com/astral-sh/ruff/blob/main/LICENSE) |
| mypy | `>=1.11,<2` | MIT | [项目](https://github.com/python/mypy) / [LICENSE](https://github.com/python/mypy/blob/master/LICENSE) |
| TypeScript | `~5.7.3` | Apache-2.0 | [项目](https://github.com/microsoft/TypeScript) / [LICENSE](https://github.com/microsoft/TypeScript/blob/main/LICENSE.txt) |
| Vite | `^6.1.0` | MIT | [项目](https://github.com/vitejs/vite) / [LICENSE](https://github.com/vitejs/vite/blob/main/LICENSE) |
| @vitejs/plugin-react | `^4.3.4` | MIT | [项目](https://github.com/vitejs/vite-plugin-react) / [LICENSE](https://github.com/vitejs/vite-plugin-react/blob/main/LICENSE) |
| @types/react / @types/react-dom | `^18.3.x` | MIT | [DefinitelyTyped](https://github.com/DefinitelyTyped/DefinitelyTyped) / [LICENSE](https://github.com/DefinitelyTyped/DefinitelyTyped/blob/master/LICENSE) |
| Inno Setup 6 | 外部安装工具 | Inno Setup License | [项目与许可](https://jrsoftware.org/isinfo.php) |

## 发布合规要求

每个可分发版本至少应归档：

1. 最终 Python/npm/原生依赖的 SPDX 或 CycloneDX SBOM；
2. 每个已分发组件的许可证原文、NOTICE 和版权声明；
3. PyInstaller bootloader exception 与 Inno Setup 许可文本；
4. 依赖版本、哈希、来源、构建工具版本和漏洞扫描结果；
5. 对 MPL 组件修改及对应源代码提供义务的审查记录；
6. 许可证扫描无法识别、无许可证或非商业条款组件的阻断记录。

本项目不会因为某个仓库“公开可见”就推定其可商用。未明确授权的代码不进入发行制品。
