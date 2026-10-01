ARG LINUX_PACKAGE_IMAGE=someip-agent-linux:package
ARG LINUX_RUNTIME_BASE=debian:bookworm-slim
FROM ${LINUX_PACKAGE_IMAGE} AS artifact
FROM ${LINUX_RUNTIME_BASE}
COPY --from=artifact /opt/linux-package/release /opt/someip-agent
ENV SOMEIP_AGENT_OPEN_BROWSER=false SOMEIP_AGENT_DATA_DIR=/var/lib/someip-agent
CMD ["/opt/someip-agent/someip-agent"]
