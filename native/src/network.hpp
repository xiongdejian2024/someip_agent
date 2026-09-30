#pragma once
#include "codec.hpp"
#include <map>
#include <set>

namespace agent {
class NetworkListener;
class PassiveCapture;

// 端口监听不是混杂模式网卡抓包；此处不初始化服务或抢占 vsomeip 的路由端点。
class NetworkRuntime {
    boost::asio::io_context &io_;
    std::map<std::string, std::shared_ptr<NetworkListener>> listeners_;
    std::map<std::string, std::shared_ptr<PassiveCapture>> captures_;
    std::vector<std::weak_ptr<Connection>> monitors_;
    void emit(const Json &);
public:
    explicit NetworkRuntime(boost::asio::io_context &io) : io_(io) {}
    ~NetworkRuntime();
    void control(const Json &, std::shared_ptr<Connection>);
    void shutdown();
};
}
