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
# 独立依赖层保留已构建的固定 vsomeip，不因抓包依赖重新编译整个上游。
RUN apt-get update && apt-get install -y --no-install-recommends libtins-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY native /workspace/native
RUN cmake -S native -B build/native -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    && cmake --build build/native -j1 \
    && ctest --test-dir build/native --output-on-failure
COPY backend /workspace/backend
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install '/workspace/backend[dev]'
ENV PATH=/opt/venv/bin:$PATH SOMEIP_AGENT_NATIVE_BINARY=/workspace/build/native/soa_partner
CMD ["/workspace/build/native/soa_partner"]
