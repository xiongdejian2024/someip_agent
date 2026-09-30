#include "capture_processor.hpp"
#include <tins/tins.h>

namespace agent {
namespace {
std::string endpoint(const std::string &host, uint16_t port) {
    return (host.find(':') == std::string::npos ? host : "[" + host + "]") + ":" + std::to_string(port);
}
std::pair<std::string, std::string> endpoints(Tins::PDU &packet, uint16_t source, uint16_t destination) {
    if (auto ip = packet.find_pdu<Tins::IP>())
        return {endpoint(ip->src_addr().to_string(), source), endpoint(ip->dst_addr().to_string(), destination)};
    auto &ip = packet.rfind_pdu<Tins::IPv6>();
    return {endpoint(ip.src_addr().to_string(), source), endpoint(ip.dst_addr().to_string(), destination)};
}
}
CaptureProcessor::CaptureProcessor(std::function<void(const Json &)> emit,
                                   std::function<void(const std::exception &)> error)
    : emit_(std::move(emit)), error_(std::move(error)), fragments_(error_) {
    follower_.follow_partial_streams(true);
    follower_.stream_keep_alive(std::chrono::seconds(60));
    follower_.new_stream_callback([this](Stream &stream) {
        if (flows_.size() >= 128) throw CaptureLimitError("抓包 TCP 流数超过 128，停止本捕获以限制资源");
        flows_.emplace(&stream, Flow{});
        stream.auto_cleanup_payloads(true);
        stream.client_data_callback([this](Stream &s) { data(s, true); });
        stream.server_data_callback([this](Stream &s) { data(s, false); });
        stream.stream_closed_callback([this](Stream &s) { close(s, false); });
    });
    follower_.stream_termination_callback([this](Stream &stream, auto reason) {
        if (reason != Tins::TCPIP::StreamFollower::TIMEOUT)
            error_(std::runtime_error("libtins 因 TCP 重组缓存限制终止流"));
        close(stream, true);
    });
}
void CaptureProcessor::close(Stream &stream, bool terminated) {
    auto found = flows_.find(&stream);
    if (found == flows_.end()) return;
    const auto &flow = found->second;
    if ((!flow.client.empty() && !flow.client_failed) || (!flow.server.empty() && !flow.server_failed))
        error_(std::runtime_error(terminated ? "TCP 捕获流超时，残留不完整 SOME/IP 报文" : "TCP 关闭时 SOME/IP 报文被截断"));
    flows_.erase(found);
}
void CaptureProcessor::check_budget() const {
    size_t bytes = 0;
    for (const auto &[stream, flow] : flows_) {
        bytes += flow.client.size() + flow.server.size();
        bytes += stream->client_flow().total_buffered_bytes() + stream->server_flow().total_buffered_bytes();
    }
    if (bytes > 16 * 1024 * 1024) throw CaptureLimitError("抓包 TCP 重组总缓存超过 16MiB");
}
void CaptureProcessor::data(Stream &stream, bool client) {
    auto &flow = flows_.at(&stream);
    auto &buffer = client ? flow.client : flow.server;
    auto &failed = client ? flow.client_failed : flow.server_failed;
    if (failed) return;
    const auto &payload = client ? stream.client_payload() : stream.server_payload();
    buffer.insert(buffer.end(), payload.begin(), payload.end());
    if (buffer.size() > max_someip_packet) throw CaptureLimitError("抓包单向 TCP 帧缓存超限");
    try {
        size_t offset = 0;
        while (buffer.size() - offset >= 16) {
            auto size = wire_length(buffer.data() + offset);
            if (size > buffer.size() - offset) break;
            auto message = decode_packet(buffer.data() + offset, size);
            auto source = stream.is_v6() ? stream.client_addr_v6().to_string() : stream.client_addr_v4().to_string();
            auto destination = stream.is_v6() ? stream.server_addr_v6().to_string() : stream.server_addr_v4().to_string();
            auto src = endpoint(source, stream.client_port()), dst = endpoint(destination, stream.server_port());
            message.update({{"source", client ? src : dst}, {"destination", client ? dst : src},
                {"transport", "tcp"}, {"received_at_ns", timestamp_ns_},
                {"ip_reassembled",current_fragments_.reassembled},{"ip_fragment_count",current_fragments_.fragment_count},
                {"tcp_partial", stream.is_partial_stream()}});
            emit_(message);
            offset += size;
        }
        buffer.erase(buffer.begin(), buffer.begin() + offset);
    } catch (const std::exception &error) {
        failed = true;
        buffer.clear();
        if (client) stream.ignore_client_data(); else stream.ignore_server_data();
        error_(error); // 无法定位下一帧时不猜测重同步，隔离该方向，不影响其他流。
    }
}
void CaptureProcessor::feed(const uint8_t *bytes, size_t size, int64_t timestamp_ns) {
    timestamp_ns_ = timestamp_ns;
    Tins::EthernetII ethernet(bytes, size);
    current_fragments_=fragments_.process(ethernet);
    if(current_fragments_.pending)return;
    if (!ethernet.find_pdu<Tins::IP>()) {
        auto ipv6=ethernet.find_pdu<Tins::IPv6>();
        if(!ipv6)return;
        if (ipv6->search_header(Tins::IPv6::FRAGMENT))
            throw std::runtime_error("捕获到 IPv6 分片，尚未支持 IP 分片重组");
    }
    if (auto udp = ethernet.find_pdu<Tins::UDP>()) {
        auto raw = udp->find_pdu<Tins::RawPDU>();
        if (!raw) throw std::runtime_error("捕获 UDP 数据报无 SOME/IP 内容");
        const auto &payload = raw->payload();
        auto [source, destination] = endpoints(ethernet, udp->sport(), udp->dport());
        size_t offset = 0;
        while (offset < payload.size()) {
            if (payload.size() - offset < 16) throw std::runtime_error("捕获 UDP SOME/IP 头被截断");
            auto length = wire_length(payload.data() + offset);
            if (length > payload.size() - offset) throw std::runtime_error("捕获 UDP SOME/IP payload 被截断");
            auto message = decode_packet(payload.data() + offset, length);
            message.update({{"source", source}, {"destination", destination}, {"transport", "udp"},
                {"ip_reassembled",current_fragments_.reassembled},{"ip_fragment_count",current_fragments_.fragment_count},
                {"received_at_ns", timestamp_ns_}, {"tcp_partial", false}});
            emit_(message);
            offset += length;
        }
    } else if (ethernet.find_pdu<Tins::TCP>()) {
        Tins::Packet packet(ethernet, Tins::Timestamp(std::chrono::microseconds(timestamp_ns / 1000)));
        follower_.process_packet(packet);
        check_budget();
    }
}
}
