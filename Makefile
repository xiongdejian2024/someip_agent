PYTHON ?= python3
NPM ?= npm
VENV ?= .venv
NATIVE_IMAGE ?= someip-agent-vsomeip:test
NATIVE_BASE ?= debian:bookworm-slim
NATIVE_SDK ?= upstream
NATIVE_INSTALLED_EVIDENCE ?= build/installed-evidence
NATIVE_PERFORMANCE_EVIDENCE ?= build/performance-evidence
LINUX_PACKAGE_IMAGE ?= someip-agent-linux:package
LINUX_UPDATE_EVIDENCE ?= build/linux-update-evidence
LINUX_CLEAN_IMAGE ?= someip-agent-linux:runtime
LINUX_CLEAN_EVIDENCE ?= build/linux-clean-evidence
LINUX_RUNTIME_BASE ?= debian:bookworm-slim
LINUX_BROWSER_IMAGE ?= someip-agent-linux:browser-test
LINUX_BROWSER_EVIDENCE ?= build/browser-update-evidence
LINUX_BROWSER_FIXTURE ?= $(LINUX_BROWSER_EVIDENCE)/fixture/new
NATIVE_IPV6 := --sysctl net.ipv6.conf.all.disable_ipv6=0 --sysctl net.ipv6.conf.default.disable_ipv6=0 --sysctl net.ipv6.conf.lo.disable_ipv6=0

ifeq ($(OS),Windows_NT)
VENV_PYTHON := $(VENV)/Scripts/python.exe
else
VENV_PYTHON := $(VENV)/bin/python
endif

.PHONY: help install install-backend install-frontend check-version lint test typecheck build-frontend ci dev-backend dev-frontend docker-up docker-down package-windows native-image native-compile native-test native-regression native-installed-test native-performance package-linux linux-update-test linux-clean-test linux-browser-image linux-browser-update-test

help:
	@echo "install          安装后端开发依赖和前端依赖"
	@echo "check-version    校验四处语义版本一致"
	@echo "lint             运行后端静态检查"
	@echo "test             运行后端测试"
	@echo "typecheck        运行前端类型检查"
	@echo "build-frontend   构建前端"
	@echo "ci               执行本地 CI 核心检查"
	@echo "dev-backend      启动 FastAPI 开发服务（8765）"
	@echo "dev-frontend     启动 Vite 开发服务（5173）"
	@echo "docker-up        启动 Docker 开发环境"
	@echo "native-image     构建 vsomeip 固定版本的 Linux 验收镜像"
	@echo "native-test      编译原生核心并运行隔离虚拟以太网功能测试"
	@echo "native-regression 在真实原生运行时上执行全部后端测试，禁止跳过"
	@echo "native-installed-test 不挂载产品源码，验收镜像内已安装的包与二进制"
	@echo "native-performance 不挂载产品源码，记录 RPC/逐条通知/原生发生器负载证据"
	@echo "package-linux    复用已验证原生镜像构建 Linux 完整发行包"
	@echo "linux-update-test 隔离测试真实发行包的签名升级、重启与失败回滚"
	@echo "linux-clean-test  无 Python/SDK 的干净 Linux 镜像验收"
	@echo "linux-browser-update-test 真实浏览器点击发行包升级，仅映射本机回环并禁止容器外连"

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)

install: install-backend install-frontend

install-backend: $(VENV_PYTHON)
	$(VENV_PYTHON) -m pip install -e './backend[dev]'

install-frontend:
	$(NPM) --prefix frontend ci --no-audit --no-fund

check-version: $(VENV_PYTHON)
	$(VENV_PYTHON) scripts/check_version.py

lint: $(VENV_PYTHON)
	$(VENV_PYTHON) -m ruff check backend/src backend/tests scripts/check_version.py scripts/create_update_manifest.py

test: $(VENV_PYTHON)
	$(VENV_PYTHON) -m pytest backend/tests

typecheck:
	$(NPM) --prefix frontend run typecheck

build-frontend:
	$(NPM) --prefix frontend run build

ci: check-version lint test typecheck build-frontend

dev-backend: $(VENV_PYTHON)
	SOMEIP_AGENT_HOST=127.0.0.1 SOMEIP_AGENT_PORT=8765 $(VENV_PYTHON) -m uvicorn someip_agent.main:app --host 127.0.0.1 --port 8765 --reload --app-dir backend/src

dev-frontend:
	VITE_BACKEND_URL=http://127.0.0.1:8765 $(NPM) --prefix frontend run dev

docker-up:
	docker compose up --build

docker-down:
	docker compose down

package-windows:
	powershell -ExecutionPolicy Bypass -File packaging/windows/build.ps1 -Clean

native-image:
	docker build --build-arg NATIVE_BASE=$(NATIVE_BASE) --build-arg NATIVE_SDK=$(NATIVE_SDK) -f native/Dockerfile -t $(NATIVE_IMAGE) .

native-compile:
	mkdir -p build/native build/virtual-evidence
	docker run --rm --init --network none -v "$(CURDIR):/workspace" $(NATIVE_IMAGE) bash -c 'cmake -S native -B build/native -DCMAKE_BUILD_TYPE=RelWithDebInfo && cmake --build build/native -j2 && ctest --test-dir build/native --output-on-failure'

native-test: native-compile
	docker run --rm --init --privileged --network none $(NATIVE_IPV6) -v "$(CURDIR):/workspace" -e PYTHONPATH=/workspace/backend/src -e SOMEIP_AGENT_EVIDENCE_OWNER="$$(id -u):$$(id -g)" $(NATIVE_IMAGE) bash native/tests/run_virtual.sh

native-regression: native-compile
	docker run --rm --init --network none $(NATIVE_IPV6) -v "$(CURDIR):/workspace" -e PYTHONPATH=/workspace/backend/src -e SOMEIP_AGENT_NATIVE_BINARY=/workspace/build/native/soa_partner -e SOMEIP_AGENT_REQUIRE_NATIVE_TESTS=1 $(NATIVE_IMAGE) python -m pytest backend/tests --junitxml=build/virtual-evidence/backend-regression.xml

native-installed-test:
	mkdir -p $(NATIVE_INSTALLED_EVIDENCE)
	docker run --rm --init --privileged --network none $(NATIVE_IPV6) \
	  -v "$(CURDIR)/native/tests:/workspace/native/tests:ro" \
	  -v "$(CURDIR)/backend/tests:/workspace/backend/tests:ro" \
	  -v "$(CURDIR)/$(NATIVE_INSTALLED_EVIDENCE):/workspace/build/virtual-evidence" \
	  -e SOMEIP_AGENT_EVIDENCE_OWNER="$$(id -u):$$(id -g)" \
	  $(NATIVE_IMAGE) bash native/tests/run_installed.sh

native-performance:
	mkdir -p $(NATIVE_PERFORMANCE_EVIDENCE)
	docker run --rm --init --privileged --network none $(NATIVE_IPV6) \
	  -v "$(CURDIR)/native/tests:/workspace/native/tests:ro" \
	  -v "$(CURDIR)/$(NATIVE_PERFORMANCE_EVIDENCE):/workspace/build/performance-evidence" \
	  -e SOMEIP_AGENT_EVIDENCE_OWNER="$$(id -u):$$(id -g)" \
	  $(NATIVE_IMAGE) bash native/tests/run_performance.sh $(PERFORMANCE_ARGS)

package-linux: build-frontend
	docker build --build-arg NATIVE_IMAGE=$(NATIVE_IMAGE) -f packaging/linux/Dockerfile -t $(LINUX_PACKAGE_IMAGE) .

linux-browser-image:
	docker build --build-arg LINUX_PACKAGE_IMAGE=$(LINUX_PACKAGE_IMAGE) -f packaging/linux/Dockerfile.browser-test -t $(LINUX_BROWSER_IMAGE) .

linux-browser-update-test:
	mkdir -p $(LINUX_BROWSER_EVIDENCE)
	docker run --rm --init -it --cap-add NET_ADMIN --network bridge \
	  --sysctl net.ipv6.conf.all.disable_ipv6=1 \
	  --publish 127.0.0.1:18765:18765 \
	  -v "$(CURDIR)/native/tests:/workspace/native/tests:ro" \
	  -v "$(CURDIR)/backend/tests:/workspace/backend/tests:ro" \
	  -v "$(CURDIR)/$(LINUX_BROWSER_EVIDENCE):/workspace/build/browser-update-evidence" \
	  -v "$(CURDIR)/$(LINUX_BROWSER_FIXTURE):/workspace/build/browser-update-evidence/fixture/new:ro" \
	  -e SOMEIP_AGENT_EVIDENCE_OWNER="$$(id -u):$$(id -g)" \
	  $(LINUX_BROWSER_IMAGE) bash native/tests/run_browser_update.sh $(BROWSER_UPDATE_CASE)

linux-update-test:
	mkdir -p $(LINUX_UPDATE_EVIDENCE)
	docker run --rm --init --network none \
	  -v "$(CURDIR)/backend/tests:/workspace/backend/tests:ro" \
	  -v "$(CURDIR)/native/tests/run_linux_update.sh:/workspace/native/tests/run_linux_update.sh:ro" \
	  -v "$(CURDIR)/$(LINUX_UPDATE_EVIDENCE):/workspace/build/linux-update-evidence" \
	  -e SOMEIP_AGENT_EVIDENCE_OWNER="$$(id -u):$$(id -g)" \
	  $(LINUX_PACKAGE_IMAGE) bash native/tests/run_linux_update.sh

linux-clean-test:
	docker build --build-arg LINUX_PACKAGE_IMAGE=$(LINUX_PACKAGE_IMAGE) --build-arg LINUX_RUNTIME_BASE=$(LINUX_RUNTIME_BASE) -f packaging/linux/runtime.Dockerfile -t $(LINUX_CLEAN_IMAGE) .
	bash native/tests/run_linux_clean.sh $(LINUX_CLEAN_IMAGE) $(LINUX_CLEAN_EVIDENCE)
