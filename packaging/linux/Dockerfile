ARG NATIVE_IMAGE=someip-agent-vsomeip:test
FROM ${NATIVE_IMAGE}
WORKDIR /workspace
RUN apt-get update && apt-get install -y --no-install-recommends libpython3.11 \
    && rm -rf /var/lib/apt/lists/*
COPY backend /workspace/backend
RUN python -m pip install '/workspace/backend[packaging]' 'hatchling>=1.27'
COPY packaging/linux /workspace/packaging/linux
COPY scripts/check_version.py /workspace/scripts/check_version.py
COPY docs/native-dependencies.md /workspace/docs/native-dependencies.md
COPY native/patches /workspace/native/patches
COPY frontend/dist /workspace/frontend/dist
COPY frontend/package.json /workspace/frontend/package.json
COPY VERSION /workspace/VERSION
RUN python packaging/linux/build.py --output /opt/linux-package --native-binary /workspace/build/native/soa_partner
CMD ["/opt/linux-package/release/someip-agent"]
