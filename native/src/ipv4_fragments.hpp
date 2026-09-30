#pragma once
#include "wire.hpp"
#include <tins/ip_reassembler.h>
#include <chrono>
#include <memory>
#include <optional>

namespace agent {
class CaptureLimitError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};
struct FragmentResult {
    bool pending = false, reassembled = false;
    size_t fragment_count = 0;
};

// libtins 负责重组；本层仅隔离完整键、限制资源并拒绝有歧义的输入。
class IPv4Fragments {
    using Clock = std::chrono::steady_clock;
    using Key = std::tuple<uint32_t,uint32_t,uint16_t,uint8_t,std::vector<uint16_t>>;
    struct Datagram {
        Tins::IPv4Reassembler reassembler;
        std::map<uint32_t,Bytes> pieces;
        std::optional<uint32_t> final_size;
        std::optional<uint32_t> first_header_size;
        Clock::time_point expires;
        size_t bytes = 0;
        bool rejected = false;
    };
    std::map<Key,std::unique_ptr<Datagram>> datagrams_;
    std::function<void(const std::exception &)> error_;
    size_t bytes_ = 0;
    uint64_t reassembled_ = 0, errors_ = 0;
    void reject(Datagram &, const char *);
public:
    explicit IPv4Fragments(std::function<void(const std::exception &)> error) : error_(std::move(error)) {}
    FragmentResult process(Tins::PDU &, Clock::time_point now = Clock::now());
    void expire(Clock::time_point now = Clock::now());
    size_t pending() const { return datagrams_.size(); }
    size_t buffered_bytes() const { return bytes_; }
    uint64_t reassembled_count() const { return reassembled_; }
    uint64_t error_count() const { return errors_; }
};
}
