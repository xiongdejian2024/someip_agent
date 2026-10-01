ARG NATIVE_BASE=debian:bookworm-slim
ARG NATIVE_SDK=upstream
FROM ${NATIVE_BASE} AS upstream
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates libboost-system-dev libboost-thread-dev \
    libboost-filesystem-dev nlohmann-json3-dev libpcap-dev python3 python3-venv \
    iproute2 tcpdump procps && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
ARG VSOMEIP_TAG=3.5.10
RUN git clone --depth 1 --branch ${VSOMEIP_TAG} https://github.com/COVESA/vsomeip.git vsomeip \
    && test "$(git -C vsomeip rev-parse HEAD)" = c4e0db329da9b63f511f3c2456c040582daf9305 \
    && cmake -S vsomeip -B vsomeip-build -DCMAKE_BUILD_TYPE=Release \
       -DENABLE_SIGNAL_HANDLING=ON -DDISABLE_DLT=ON -DBUILD_TESTING=OFF \
    && cmake --build vsomeip-build --target install -j1 && ldconfig
# 默认使用上游阶段；已有同提交 SDK 可显式传入以便离线复用，桥接阶段仍核验源码提交。
FROM ${NATIVE_SDK} AS build
RUN test "$(git -C /opt/vsomeip rev-parse HEAD)" = c4e0db329da9b63f511f3c2456c040582daf9305
# 独立抓包依赖层：固定维护版本和局部 RFC 791 选项补丁，不静默改写原始报文。
COPY native/patches/libtins-ipv4-options.patch /opt/libtins-ipv4-options.patch
RUN git clone --depth 1 --branch v4.6 https://github.com/mfontanini/libtins.git /opt/libtins \
    && test "$(git -C /opt/libtins rev-parse HEAD)" = 2d2f7012d9f3a16d684a55ba39f1215b6aef5429 \
    && git -C /opt/libtins apply --check /opt/libtins-ipv4-options.patch \
    && git -C /opt/libtins apply /opt/libtins-ipv4-options.patch \
    && test "$(sha256sum /opt/libtins/src/ip.cpp | cut -d ' ' -f1)" = fe3b9717eb8b53e5fef9736854cf5fb18f055139f2a812b4e7e24ffd0e26f486 \
    && cmake -S /opt/libtins -B /opt/libtins-build -DCMAKE_BUILD_TYPE=Release \
       -DLIBTINS_BUILD_EXAMPLES=OFF -DLIBTINS_BUILD_TESTS=OFF -DLIBTINS_ENABLE_DOT11=OFF \
       -DLIBTINS_ENABLE_TCPIP=ON -DLIBTINS_ENABLE_PCAP=ON \
    && cmake --build /opt/libtins-build --target install -j2 && ldconfig \
    && mkdir -p /opt/third-party/libtins \
    && cp /opt/libtins/LICENSE /opt/libtins-ipv4-options.patch /opt/third-party/libtins/
WORKDIR /workspace
COPY native /workspace/native
RUN cmake -S native -B build/native -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    && cmake --build build/native -j1 \
    && ctest --test-dir build/native --output-on-failure
COPY backend /workspace/backend
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install '/workspace/backend[dev]'
# 完整后端回归中的升级夹具仅需这两份测试辅助文件；产品仍从 wheel 加载。
COPY scripts/check_version.py /workspace/scripts/check_version.py
COPY packaging/linux/prepare_update_fixture.py /workspace/packaging/linux/prepare_update_fixture.py
ENV PATH=/opt/venv/bin:$PATH SOMEIP_AGENT_NATIVE_BINARY=/workspace/build/native/soa_partner
CMD ["/workspace/build/native/soa_partner"]
