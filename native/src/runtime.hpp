#pragma once
#include "application.hpp"
#include <vsomeip/vsomeip.hpp>
#include <map>
#include <set>
#include <thread>

namespace agent {
struct Api {
    std::string name;
    uint16_t id;
    bool event = false, field = false, fire_and_forget = false;
    std::set<vsomeip::eventgroup_t> groups;
    Json input = {{"type","struct"},{"fields",Json::array()}};
    Json output = input;
};
struct Member {
    std::string key, role, name, state = "OFFLINE";
    std::shared_ptr<vsomeip::application> application;
    uint16_t service, instance;
    uint8_t major;
    uint32_t minor;
    bool reliable = false, active = true;
    std::map<std::string, Api> apis;
    std::set<std::string> subscriptions;
    // 仅保留协议栈已交付的字段最新值，跨越成员 socket 建立前的短窗口；普通事件不回放。
    std::map<std::string, Json> field_values;
    std::set<std::string> pending_initial_fields;
    std::shared_ptr<Listener> listener;
    std::vector<std::weak_ptr<Connection>> connections;
    std::map<std::string,std::deque<std::shared_ptr<vsomeip::message>>> requests;
    std::shared_ptr<boost::asio::steady_timer> timer;
    uint64_t count = 0;
    uint64_t generator_epoch = 0;
    double last_value = 0;
    std::shared_ptr<boost::asio::steady_timer> event_timer;
    uint64_t event_epoch = 0, event_count = 0;
    bool event_running = false;
    std::string event_function;
    Bytes event_payload;
    std::chrono::milliseconds event_interval{1000};
    void emit(const Json &message);
};
class Runtime {
    boost::asio::io_context &io_;
    Json catalog_;
    std::string bind_, default_application_;
    std::map<std::string,uint16_t> configured_applications_;
    std::map<std::string,std::unique_ptr<ApplicationStack>> applications_;
    std::set<std::string> allowed_subscribers_;
    bool restricted_ = false, shutting_down_ = false;
    std::shared_ptr<vsomeip::application> application(const std::string &);
    std::map<std::string,std::shared_ptr<Member>> members_;
    struct Pending {
        std::weak_ptr<Connection> connection;
        std::shared_ptr<Member> member;
        std::string function;
        std::string correlation;
        std::shared_ptr<boost::asio::steady_timer> timer;
    };
    std::map<uint32_t,Pending> pending_;
    std::vector<std::weak_ptr<Connection>> monitors_;
    void configure(const std::string &, const Json &);
    void stop_member(const std::string &);
    void command(std::shared_ptr<Member>, const Json &, std::shared_ptr<Connection>);
    void receive(const std::string &,std::shared_ptr<vsomeip::message>);
    void subscribe(std::shared_ptr<Member>, const std::string &, bool);
    bool has_other_event_consumer(const std::shared_ptr<Member> &, uint16_t) const;
    void notify(std::shared_ptr<Member>, const Api &, const Bytes &);
    void generator(std::shared_ptr<Member>, std::shared_ptr<const Json>, uint64_t, std::chrono::steady_clock::time_point,
                   std::chrono::steady_clock::time_point);
    void event_cycle(std::shared_ptr<Member>, uint64_t, std::chrono::steady_clock::time_point);
    void trace(std::shared_ptr<Member>, const Api &, const Bytes &, const std::string &,
               uint8_t, uint16_t=0, uint16_t=0, uint8_t=0);
public:
    Runtime(boost::asio::io_context &, Json, Json, std::string, std::string);
    ~Runtime();
    void control(const Json &, std::shared_ptr<Connection>);
    void shutdown();
};
}
