#include "capture.hpp"

namespace agent {
Json capture_interfaces() {
    pcap_if_t *devices = nullptr;
    char error[PCAP_ERRBUF_SIZE]{};
    if (pcap_findalldevs(&devices, error) != 0) throw std::runtime_error("枚举抓包网卡失败: " + std::string(error));
    std::unique_ptr<pcap_if_t, decltype(&pcap_freealldevs)> owner(devices, pcap_freealldevs);
    Json result = Json::array();
    for (auto device = devices; device; device = device->next) {
        Json addresses = Json::array();
        for (auto item = device->addresses; item; item = item->next) {
            if (!item->addr) continue;
            if (item->addr->sa_family == AF_INET) {
                auto address = reinterpret_cast<sockaddr_in *>(item->addr);
                addresses.push_back(boost::asio::ip::address_v4(ntohl(address->sin_addr.s_addr)).to_string());
            } else if (item->addr->sa_family == AF_INET6) {
                auto address = reinterpret_cast<sockaddr_in6 *>(item->addr);
                boost::asio::ip::address_v6::bytes_type bytes{};
                std::copy_n(reinterpret_cast<const uint8_t *>(&address->sin6_addr), 16, bytes.begin());
                addresses.push_back(boost::asio::ip::address_v6(bytes, address->sin6_scope_id).to_string());
            }
        }
        result.push_back({{"name", device->name}, {"description", device->description ? device->description : ""},
            {"loopback", bool(device->flags & PCAP_IF_LOOPBACK)}, {"addresses", addresses}});
    }
    return result;
}
PassiveCapture::PassiveCapture(boost::asio::io_context &io, std::string id, const Json &config,
                               std::function<void(const Json &)> emit)
    : timer_(io), id_(std::move(id)), interface_(config.at("capture_interface")), emit_(std::move(emit)) {
    if (interface_.empty() || interface_.size() > 256) throw std::runtime_error("被动抓包必须指定有效网卡名称");
    auto filter = config.at("capture_filter").get<std::string>();
    if (filter.empty() || filter.size() > 4096) throw std::runtime_error("被动抓包必须指定不超过 4096 字符的 BPF 过滤器");
    char error[PCAP_ERRBUF_SIZE]{};
    handle_.reset(pcap_create(interface_.c_str(), error));
    if (!handle_) throw std::runtime_error("创建被动抓包句柄失败: " + std::string(error));
    if (pcap_set_snaplen(handle_.get(), 65535) || pcap_set_promisc(handle_.get(), config.value("promiscuous", false)) ||
        pcap_set_timeout(handle_.get(), 20) || pcap_set_immediate_mode(handle_.get(), 1) ||
        pcap_set_buffer_size(handle_.get(), 4 * 1024 * 1024)) throw std::runtime_error("配置 libpcap 抓包句柄失败");
    auto activation = pcap_activate(handle_.get());
    if (activation < 0) throw std::runtime_error("启动被动抓包失败: " + std::string(pcap_geterr(handle_.get())));
    if (activation > 0) std::cout << Json{{"operation", "capture.warning"},
        {"message", "libpcap 激活警告: " + std::string(pcap_statustostr(activation))}}.dump() << std::endl;
    if (pcap_datalink(handle_.get()) != DLT_EN10MB) throw std::runtime_error("当前被动抓包需要 Ethernet 链路类型，不支持此网卡");
    bpf_program program{};
    if (pcap_compile(handle_.get(), &program, filter.c_str(), 1, PCAP_NETMASK_UNKNOWN))
        throw std::runtime_error("BPF 过滤器编译失败: " + std::string(pcap_geterr(handle_.get())));
    auto result = pcap_setfilter(handle_.get(), &program);
    pcap_freecode(&program);
    if (result) throw std::runtime_error("BPF 过滤器应用失败: " + std::string(pcap_geterr(handle_.get())));
    if (pcap_setnonblock(handle_.get(), 1, error)) throw std::runtime_error("设置非阻塞抓包失败: " + std::string(error));
    processor_ = std::make_unique<CaptureProcessor>([this](const Json &packet) {
        auto message = packet;
        ++count_;
        message.update(stats());
        message.update({{"action", "packet"}, {"observation", "pcap_capture"}, {"capture_interface", interface_}});
        emit_(message);
    }, [this](const std::exception &error) { this->error(error, false); });
}
Json PassiveCapture::stats() const {
    pcap_stat counters{};
    bool available = handle_ && pcap_stats(handle_.get(), &counters) == 0;
    return {{"listener_id", id_}, {"received_count", count_}, {"parse_error_count", parse_errors_},
        {"captured_count", captured_}, {"active_streams", processor_ ? processor_->active_streams() : 0},
        {"active_fragment_datagrams",processor_ ? processor_->active_fragment_datagrams() : 0},
        {"fragment_buffered_bytes",processor_ ? processor_->fragment_buffered_bytes() : 0},
        {"reassembled_datagrams",processor_ ? processor_->reassembled_datagrams() : 0},
        {"fragment_error_count",processor_ ? processor_->fragment_error_count() : 0},
        {"kernel_dropped_count", available ? Json(counters.ps_drop) : Json(nullptr)},
        {"interface_dropped_count", available ? Json(counters.ps_ifdrop) : Json(nullptr)}, {"running", active_}};
}
void PassiveCapture::error(const std::exception &error, bool fatal) {
    log_error(fatal ? "capture.receive" : "capture.decode", error);
    if (!fatal) ++parse_errors_;
    auto message = stats();
    if (fatal) { close();message["running"] = false;message["active_streams"] = 0;
        message["active_fragment_datagrams"]=0;message["fragment_buffered_bytes"]=0; }
    message.update({{"action", "listener_error"}, {"error", error.what()}});
    emit_(message);
}
void PassiveCapture::poll() {
    if (!active_) return;
    try {
        processor_->maintenance();
        for (size_t i = 0; i < 64; ++i) {
            pcap_pkthdr *header = nullptr;
            const u_char *data = nullptr;
            auto result = pcap_next_ex(handle_.get(), &header, &data);
            if (!result) break;
            if (result < 0) throw CaptureLimitError("被动抓包读取失败: " + std::string(pcap_geterr(handle_.get())));
            ++captured_;
            try {
                if (header->caplen != header->len) throw std::runtime_error("捕获 Ethernet 帧被 snaplen 截断");
                processor_->feed(data, header->caplen, int64_t(header->ts.tv_sec) * 1000000000 + int64_t(header->ts.tv_usec) * 1000);
            } catch (const CaptureLimitError &) { throw; }
            catch (const std::exception &error) { this->error(error, false); }
        }
        auto now = std::chrono::steady_clock::now();
        if (now >= next_stats_) {
            auto message = stats();message["action"] = "listener_stats";emit_(message);
            next_stats_ = now + std::chrono::seconds(1);
        }
        timer_.expires_after(std::chrono::milliseconds(5));
        auto self = shared_from_this();
        timer_.async_wait([self](auto error) { if (!error) self->poll(); });
    } catch (const std::exception &error) { this->error(error, true); }
}
void PassiveCapture::start() {
    auto self = shared_from_this();
    boost::asio::post(timer_.get_executor(), [self] { self->poll(); });
}
void PassiveCapture::close() { active_ = false;timer_.cancel();handle_.reset();processor_.reset(); }
}
