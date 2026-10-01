PYTHON ?= python3
NPM ?= npm
VENV ?= .venv
NATIVE_IMAGE ?= someip-agent-vsomeip:test
NATIVE_BASE ?= debian:bookworm-slim
NATIVE_SDK ?= upstream
NATIVE_INSTALLED_EVIDENCE ?= build/installed-evidence
NATIVE_IPV6 := --sysctl net.ipv6.conf.all.disable_ipv6=0 --sysctl net.ipv6.conf.default.disable_ipv6=0 --sysctl net.ipv6.conf.lo.disable_ipv6=0

ifeq ($(OS),Windows_NT)
VENV_PYTHON := $(VENV)/Scripts/python.exe
else
VENV_PYTHON := $(VENV)/bin/python
endif

.PHONY: help install install-backend install-frontend check-version lint test typecheck build-frontend ci dev-backend dev-frontend docker-up docker-down package-windows native-image native-compile native-test native-regression native-installed-test

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
	docker run --rm --init --network none -v "$(CURDIR):/workspace" $(NATIVE_IMAGE) bash -c 'cmake -S native -B build/native -DCMAKE_BUILD_TYPE=RelWithDebInfo && cmake --build build/native -j2 && ctest --test-dir build/native --output-on-failure'

native-test: native-compile
	docker run --rm --init --privileged --network none $(NATIVE_IPV6) -v "$(CURDIR):/workspace" -e PYTHONPATH=/workspace/backend/src $(NATIVE_IMAGE) bash native/tests/run_virtual.sh

native-regression: native-compile
	docker run --rm --init --network none $(NATIVE_IPV6) -v "$(CURDIR):/workspace" -e PYTHONPATH=/workspace/backend/src -e SOMEIP_AGENT_NATIVE_BINARY=/workspace/build/native/soa_partner -e SOMEIP_AGENT_REQUIRE_NATIVE_TESTS=1 $(NATIVE_IMAGE) python -m pytest backend/tests --junitxml=build/virtual-evidence/backend-regression.xml

native-installed-test:
	mkdir -p $(NATIVE_INSTALLED_EVIDENCE)
	docker run --rm --init --privileged --network none $(NATIVE_IPV6) \
	  -v "$(CURDIR)/native/tests:/workspace/native/tests:ro" \
	  -v "$(CURDIR)/backend/tests:/workspace/backend/tests:ro" \
	  -v "$(CURDIR)/$(NATIVE_INSTALLED_EVIDENCE):/workspace/build/virtual-evidence" \
	  $(NATIVE_IMAGE) bash native/tests/run_installed.sh
