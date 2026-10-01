#pragma once
#include "codec.hpp"
#include <vsomeip/vsomeip.hpp>
#include <set>
#include <thread>

namespace agent {
// 每个具名 application 直接复用 vsomeip 的 Client ID、路由、订阅及会话管理。
// 上下文保留到进程退出，成员重启不重置会话计数，避免旧响应污染新请求。
class ApplicationStack {
    std::shared_ptr<vsomeip::application> app_;
    std::thread thread_;
public:
    using Handler = std::function<void(std::shared_ptr<vsomeip::message>)>;
    ApplicationStack(const std::string &, uint16_t, const std::set<std::string> &, bool, Handler);
    ~ApplicationStack();
    std::shared_ptr<vsomeip::application> application() const { return app_; }
    void stop();
};
}
