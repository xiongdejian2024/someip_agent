#pragma once
#include "wire.hpp"
#include "ipv4_fragments.hpp"
#include <tins/tcp_ip/stream_follower.h>

namespace agent {
// libtins 管理 TCP 序号、重传与乱序；这里只分割已重组的 SOME/IP 长度帧。
class CaptureProcessor {
    struct Flow {
        Bytes client, server;
        bool client_failed = false, server_failed = false;
    };
    using Stream = Tins::TCPIP::Stream;
    Tins::TCPIP::StreamFollower follower_;
    std::map<Stream *, Flow> flows_;
    std::function<void(const Json &)> emit_;
    std::function<void(const std::exception &)> error_;
    IPv4Fragments fragments_;
    FragmentResult current_fragments_;
    int64_t timestamp_ns_ = 0;
    void data(Stream &, bool);
    void close(Stream &, bool);
    void check_budget() const;
public:
    CaptureProcessor(std::function<void(const Json &)>, std::function<void(const std::exception &)>);
    void feed(const uint8_t *, size_t, int64_t);
    size_t active_streams() const { return flows_.size(); }
    void maintenance() { fragments_.expire(); }
    size_t active_fragment_datagrams() const { return fragments_.pending(); }
    size_t fragment_buffered_bytes() const { return fragments_.buffered_bytes(); }
    uint64_t reassembled_datagrams() const { return fragments_.reassembled_count(); }
    uint64_t fragment_error_count() const { return fragments_.error_count(); }
};
}
