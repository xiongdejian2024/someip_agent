# syntax=docker/dockerfile:1.7

FROM node:22.19.0-bookworm-slim AS pi-build
WORKDIR /opt/pi-source
COPY agent-runtime/package.json agent-runtime/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY agent-runtime/build.mjs agent-runtime/pi-LICENSE ./
COPY agent-runtime/src ./src
RUN npm run build

FROM python:3.12-slim AS backend-dev
COPY --from=pi-build /usr/local/bin/node /usr/local/bin/node
COPY --from=pi-build /opt/pi-source/dist/runtime.mjs /opt/someip-pi/runtime.mjs
ENV SOMEIP_AGENT_PI_RUNTIME_PATH=/opt/someip-pi/runtime.mjs

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SOMEIP_AGENT_HOST=0.0.0.0 \
    SOMEIP_AGENT_PORT=8765 \
    SOMEIP_AGENT_DATA_DIR=/workspace/data

WORKDIR /workspace
COPY backend/pyproject.toml backend/README.md /workspace/backend/
COPY backend/src /workspace/backend/src
RUN python -m pip install --no-cache-dir -e '/workspace/backend[dev]'

EXPOSE 8765
CMD ["python", "-m", "uvicorn", "someip_agent.main:app", "--host", "0.0.0.0", "--port", "8765", "--reload", "--app-dir", "/workspace/backend/src"]

FROM node:22-alpine AS frontend-dev

WORKDIR /workspace/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM python:3.12-slim AS backend-runtime
COPY --from=pi-build /usr/local/bin/node /usr/local/bin/node
COPY --from=pi-build /opt/pi-source/dist/runtime.mjs /opt/someip-pi/runtime.mjs
ENV SOMEIP_AGENT_PI_RUNTIME_PATH=/opt/someip-pi/runtime.mjs

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SOMEIP_AGENT_HOST=0.0.0.0 \
    SOMEIP_AGENT_PORT=8765 \
    SOMEIP_AGENT_DATA_DIR=/var/lib/someip-agent

WORKDIR /app
COPY backend /app/backend
RUN python -m pip install --no-cache-dir /app/backend \
    && addgroup --system someip \
    && adduser --system --ingroup someip --home /var/lib/someip-agent someip \
    && mkdir -p /var/lib/someip-agent \
    && chown -R someip:someip /var/lib/someip-agent

USER someip
EXPOSE 8765
CMD ["someip-agent"]
