#pragma once
#include "codec.hpp"

namespace agent {
// payload_hex 是两倍长度，为 4MiB IPC 帧预留元数据空间。
constexpr size_t max_someip_packet = (max_frame - 4096) / 2;
uint32_t wire_length(const uint8_t *);
Json decode_packet(const uint8_t *, size_t);
}
