#!/usr/bin/env bash
# 只供 --network none 的临时验收容器调用，禁止在宿主或现有业务网卡上运行。
setup_virtual_network() {
  local node
  ip netns add soa-server
  ip netns add soa-client
  ip link add soa-bridge type bridge
  ip addr add 10.77.0.254/24 dev soa-bridge
  ip link set soa-bridge up
  for node in server client; do
    ip link add "${node}-host" type veth peer name "${node}-eth"
    ip link set "${node}-host" master soa-bridge
    ip link set "${node}-host" up
    ip link set "${node}-eth" netns "soa-${node}"
    ip -n "soa-${node}" link set lo up
    ip -n "soa-${node}" link set "${node}-eth" name eth0
    ip -n "soa-${node}" addr add "10.77.0.$([ "$node" = server ] && echo 1 || echo 2)/24" dev eth0
    ip -n "soa-${node}" -6 addr add "fd77::$([ "$node" = server ] && echo 1 || echo 2)/64" dev eth0 nodad
    ip -n "soa-${node}" link set eth0 up
    ip -n "soa-${node}" route add 224.0.0.0/4 dev eth0
  done
}
