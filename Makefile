PYTHON ?= python3
NPM ?= npm
VENV ?= .venv

ifeq ($(OS),Windows_NT)
VENV_PYTHON := $(VENV)/Scripts/python.exe
else
VENV_PYTHON := $(VENV)/bin/python
endif

.PHONY: help install install-backend install-frontend check-version lint test typecheck build-frontend ci dev-backend dev-frontend docker-up docker-down package-windows

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
