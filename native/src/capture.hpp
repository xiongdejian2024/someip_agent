#pragma once
#include "capture_processor.hpp"
#include <pcap/pcap.h>

namespace agent {
Json capture_interfaces();
class PassiveCapture : public std::enable_shared_from_this<PassiveCapture> {
    boost::asio::steady_timer timer_;
    std::string id_, interface_;
    std::unique_ptr<pcap_t, decltype(&pcap_close)> handle_{nullptr, pcap_close};
    std::unique_ptr<CaptureProcessor> processor_;
    std::function<void(const Json &)> emit_;
    bool active_ = true;
    uint64_t count_ = 0, parse_errors_ = 0, captured_ = 0;
    std::chrono::steady_clock::time_point next_stats_{};
    Json stats() const;
    void error(const std::exception &, bool);
    void poll();
public:
    PassiveCapture(boost::asio::io_context &, std::string, const Json &, std::function<void(const Json &)>);
    void start();
    void close();
};
}
