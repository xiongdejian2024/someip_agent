#include "network.hpp"
#include "capture.hpp"
#include "wire.hpp"

namespace agent {
namespace {
using Udp = boost::asio::ip::udp;
// payload_hex 膨胀为两倍长度，为 4MiB IPC 帧预留元数据空间。
constexpr size_t max_packet = max_someip_packet;
constexpr size_t max_clients = 128;

template<class Endpoint> std::string address(const Endpoint &endpoint) {
    auto host = endpoint.address().to_string();
    return (endpoint.address().is_v6() ? "[" + host + "]" : host) + ":" + std::to_string(endpoint.port());
}

}

class NetworkListener : public std::enable_shared_from_this<NetworkListener> {
    struct Peer : std::enable_shared_from_this<Peer> {
        Tcp::socket socket;
        boost::asio::steady_timer deadline;
        std::array<uint8_t, 16> header{};
        Bytes packet;
        std::weak_ptr<NetworkListener> owner;
        uint64_t read_epoch = 0;
        Peer(boost::asio::io_context &io) : socket(io), deadline(io) {}
        void close() {
            ++read_epoch;
            deadline.cancel();
            boost::system::error_code error;
            socket.close(error);
            if (auto listener = owner.lock()) listener->peers_.erase(shared_from_this());
        }
        void arm_timeout() {
            auto epoch = ++read_epoch;
            deadline.expires_after(std::chrono::seconds(10));
            auto self = shared_from_this();
            deadline.async_wait([self, epoch](auto error) {
                if (!error && self->read_epoch == epoch) {
                    if (auto listener = self->owner.lock())
                        listener->error(std::runtime_error("TCP 报文读取超时"), true);
                    self->close();
                }
            });
        }
        void fail(const boost::system::error_code &error) {
            if (auto listener = owner.lock()) {
                if (error != boost::asio::error::operation_aborted &&
                    (error != boost::asio::error::eof || !packet.empty()))
                    listener->error(std::runtime_error("TCP 接收失败或报文截断: " + error.message()), !packet.empty());
            }
            close();
        }
        void read_header() {
            packet.clear();
            auto self = shared_from_this();
            // 空闲连接可长期保留；只有开始收到一帧后才限制剩余读取时间。
            boost::asio::async_read(socket, boost::asio::buffer(header.data(), 1), [self](auto error, auto) {
                if (error) {self->fail(error);return;}
                self->packet.resize(1);
                self->arm_timeout();
                self->read_header_rest();
            });
        }
        void read_header_rest() {
            auto self = shared_from_this();
            boost::asio::async_read(socket, boost::asio::buffer(header.data() + 1, header.size() - 1), [self](auto error, auto count) {
                if (error) {
                    // 半个头后的 EOF 必须记录截断，空流正常退出不计错。
                    self->packet.resize(count + 1);
                    self->fail(error);
                    return;
                }
                try {
                    auto size = wire_length(self->header.data());
                    self->packet.assign(self->header.begin(), self->header.end());
                    self->packet.resize(size);
                    self->read_body();
                } catch (const std::exception &error) {
                    if (auto listener = self->owner.lock()) listener->error(error, true);
                    self->close();
                }
            });
        }
        void read_body() {
            auto self = shared_from_this();
            boost::asio::async_read(socket, boost::asio::buffer(packet.data() + 16, packet.size() - 16),
                [self](auto error, auto) {
                    if (error) { self->fail(error); return; }
                    self->deadline.cancel();
                    if (auto listener = self->owner.lock()) {
                        try {
                            listener->packet(self->packet.data(), self->packet.size(),
                                address(self->socket.remote_endpoint()), address(self->socket.local_endpoint()));
                        } catch (const std::exception &error) { listener->error(error, true); }
                        if (listener->active_) self->read_header();
                    }
                });
        }
    };
    boost::asio::io_context &io_;
    std::string id_, transport_;
    Udp::socket udp_;
    Tcp::acceptor tcp_;
    Udp::endpoint sender_;
    std::array<uint8_t, 65536> datagram_{};
    std::set<std::shared_ptr<Peer>> peers_;
    std::function<void(const Json &)> emit_;
    bool active_ = true;
    uint64_t count_ = 0, parse_errors_ = 0;
    std::string last_error_;
    void error(const std::exception &error, bool parse) {
        log_error(parse ? "network.decode" : "network.receive", error);
        if (parse) ++parse_errors_;
        last_error_ = error.what();
        emit_({{"action", "listener_error"}, {"listener_id", id_}, {"error", last_error_},
               {"received_count", count_}, {"parse_error_count", parse_errors_}, {"running", active_}});
    }
    void packet(const uint8_t *data, size_t size, const std::string &source, const std::string &destination) {
        auto message = decode_packet(data, size);
        message.update({{"action", "packet"}, {"listener_id", id_}, {"transport", transport_},
            {"source", source}, {"destination", destination}, {"received_count", ++count_},
            {"parse_error_count", parse_errors_}, {"observation", "socket_receive"},
            {"received_at_ns", std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count()}});
        emit_(message);
    }
    void receive_udp() {
        auto self = shared_from_this();
        udp_.async_receive_from(boost::asio::buffer(datagram_), sender_, [self](auto ec, auto size) {
            if (!self->active_) return;
            if (ec) {
                self->error(std::runtime_error("UDP 接收失败: " + ec.message()), false);
            } else {
                try {
                    size_t offset = 0;
                    while (offset < size) {
                        if (size - offset < 16) throw std::runtime_error("UDP SOME/IP 头被截断");
                        auto length = wire_length(self->datagram_.data() + offset);
                        if (length > size - offset) throw std::runtime_error("UDP SOME/IP payload 被截断");
                        self->packet(self->datagram_.data() + offset, length,
                            address(self->sender_), address(self->udp_.local_endpoint()));
                        offset += length;
                    }
                    if (!size) throw std::runtime_error("UDP 数据报为空");
                } catch (const std::exception &error) { self->error(error, true); }
            }
            self->receive_udp();
        });
    }
    void accept_tcp() {
        auto peer = std::make_shared<Peer>(io_);
        peer->owner = shared_from_this();
        auto self = shared_from_this();
        tcp_.async_accept(peer->socket, [self, peer](auto ec) {
            if (!self->active_) return;
            if (ec) self->error(std::runtime_error("TCP 接入失败: " + ec.message()), false);
            else if (self->peers_.size() >= max_clients) {
                self->error(std::runtime_error("TCP 监听连接数超限"), false);
                peer->close();
            } else { self->peers_.insert(peer); peer->read_header(); }
            self->accept_tcp();
        });
    }
public:
    NetworkListener(boost::asio::io_context &io, std::string id, const Json &config,
                    std::function<void(const Json &)> emit)
        : io_(io), id_(std::move(id)), transport_(config.value("transport", "udp")),
          udp_(io), tcp_(io), emit_(std::move(emit)) {
        auto host = boost::asio::ip::make_address(config.value("bind_host", "0.0.0.0"));
        auto port = bounded_number(config.at("port"), 65535, "监听端口");
        if (!port) throw std::runtime_error("监听端口必须为 1 至 65535");
        if (transport_ == "udp") {
            udp_.open(host.is_v6() ? Udp::v6() : Udp::v4());
            auto group = config.value("multicast_group", Json());
            if (!group.is_null() && group != "") udp_.set_option(Udp::socket::reuse_address(true));
            udp_.bind(Udp::endpoint(host, port));
            if (!group.is_null() && group != "") {
                auto multicast = boost::asio::ip::make_address_v4(group.get<std::string>());
                if (!multicast.is_multicast() || !host.is_v4())
                    throw std::runtime_error("组播监听需要 IPv4 组播地址和 IPv4 绑定地址");
                auto interface = boost::asio::ip::make_address_v4(config.value("interface_ip", "0.0.0.0"));
                udp_.set_option(boost::asio::ip::multicast::join_group(multicast, interface));
            }
        } else if (transport_ == "tcp") {
            if (!config.value("multicast_group", Json()).is_null() && config.at("multicast_group") != "")
                throw std::runtime_error("TCP 不能使用组播监听");
            tcp_.open(host.is_v6() ? Tcp::v6() : Tcp::v4());
            tcp_.set_option(Tcp::acceptor::reuse_address(true));
            tcp_.bind(Tcp::endpoint(host, port));
            tcp_.listen(128);
        } else throw std::runtime_error("监听传输协议必须为 udp/tcp");
    }
    void start() { if (transport_ == "udp") receive_udp(); else accept_tcp(); }
    void close() {
        active_ = false;
        boost::system::error_code error;
        udp_.close(error);
        tcp_.close(error);
        while (!peers_.empty()) {auto peer=*peers_.begin();peer->close();}
    }
    ~NetworkListener() { boost::system::error_code error; udp_.close(error); tcp_.close(error); }
};

NetworkRuntime::~NetworkRuntime() { shutdown(); }
void NetworkRuntime::shutdown() {
    for (auto &[id, listener] : listeners_) listener->close();
    for (auto &[id, capture] : captures_) capture->close();
    listeners_.clear();
    captures_.clear();
}
void NetworkRuntime::emit(const Json &message) {
    monitors_.erase(std::remove_if(monitors_.begin(), monitors_.end(),
        [](auto &connection) { return connection.expired(); }), monitors_.end());
    for (auto &weak : monitors_) if (auto connection = weak.lock()) connection->send(message);
}
void NetworkRuntime::control(const Json &request, std::shared_ptr<Connection> connection) {
    auto function = request.value("function", "");
    try {
        auto args = unpack_json(request.value("args", Json()));
        Json result = true;
        if (function == "monitor") { monitors_.push_back(connection); }
        else if (function == "network_interfaces") { result = capture_interfaces(); }
        else if (function == "network_start") {
            auto id = args.at("id").get<std::string>();
            if (listeners_.count(id) || captures_.count(id)) throw std::runtime_error("监听标识符已存在");
            const auto &config = args.at("config");
            auto mode = config.value("mode", "socket");
            if (mode == "pcap") {
                auto capture = std::make_shared<PassiveCapture>(io_, id, config,
                    [this](const Json &message) { emit(message); });
                captures_[id] = capture;
                capture->start();
            } else if (mode == "socket") {
                auto listener = std::make_shared<NetworkListener>(io_, id, config,
                    [this](const Json &message) { emit(message); });
                listeners_[id] = listener;
                listener->start();
            } else throw std::runtime_error("未知网络监听模式");
            std::cout << Json{{"operation", "network.start"}, {"listener_id", id},
                {"mode", mode}, {"message", "原生网络观测已启动"}}.dump() << std::endl;
        } else if (function == "network_stop") {
            auto id = args.at("id").get<std::string>();
            auto found = listeners_.find(id);
            if (found != listeners_.end()) { found->second->close(); listeners_.erase(found); }
            auto capture = captures_.find(id);
            if (capture != captures_.end()) { capture->second->close(); captures_.erase(capture); }
            std::cout << Json{{"operation", "network.stop"}, {"listener_id", id},
                {"message", "原生端口监听已停止"}}.dump() << std::endl;
        } else if (function == "ping") result = {{"runtime", "vsomeip"}, {"mode", "network"}};
        else throw std::runtime_error("未知网络控制操作: " + function);
        connection->send({{"action", "response"}, {"function", function},
            {"result", result.dump()}, {"failtype", "FAILTYPE_SUCCESS"}});
    } catch (const std::exception &error) {
        log_error("network.control." + function, error);
        connection->send({{"action", "response"}, {"function", function}, {"result", "null"},
            {"failtype", "FAILTYPE_BAD_PARAM"}, {"error", error.what()}});
    }
}
}
