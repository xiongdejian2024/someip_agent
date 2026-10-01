#pragma once
#include "capture_processor.hpp"
#include <pcap/pcap.h>

namespace agent {
// 离线读取复用实时重组与 vsomeip 解码；每次轮询有限批量，保持 IPC 队列有界。
class OfflineImport : public std::enable_shared_from_this<OfflineImport> {
    boost::asio::steady_timer timer_;
    std::unique_ptr<pcap_t,decltype(&pcap_close)> handle_{nullptr,pcap_close};
    std::unique_ptr<CaptureProcessor> processor_;
    std::weak_ptr<Connection> connection_;
    std::function<void()> finished_;
    int link_type_ = 0;
    uint64_t number_ = 0, captured_bytes_ = 0;
    std::optional<int64_t> start_ns_,end_ns_;
    Json frame_,errors_=Json::array();
    bool active_ = true;
    void poll();
    void decode(const pcap_pkthdr &,const uint8_t *);
    void error(const std::exception &);
    void done(const std::string &failure = "");
public:
    OfflineImport(boost::asio::io_context &,const std::string &,std::shared_ptr<Connection>,std::function<void()>);
    void start();
    void close();
};
}
