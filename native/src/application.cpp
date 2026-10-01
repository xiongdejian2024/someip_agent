#include "application.hpp"

namespace agent {
ApplicationStack::ApplicationStack(const std::string &name,uint16_t id,
        const std::set<std::string> &allowed,bool restricted,Handler handler)
    : app_(vsomeip::runtime::get()->create_application(name)) {
    if(app_->get_name()!=name)throw std::runtime_error("vsomeip application 名称已占用: "+name);
    if(!app_->init())throw std::runtime_error("vsomeip application 初始化失败: "+name);
    if(app_->get_client()!=id)throw std::runtime_error("vsomeip application Client ID 与配置不一致: "+name);
    if(restricted)app_->register_message_acceptance_handler([allowed,name](const vsomeip::message_acceptance_t &remote){
        auto host=boost::asio::ip::address_v4(remote.remote_address_).to_string();
        bool accepted=allowed.count(host)>0;
        std::cout<<Json{{"operation","subscription.policy"},{"application_name",name},
            {"host",host},{"accepted",accepted},
            {"message",accepted?"远端报文通过白名单":"远端报文被白名单拒绝"}}.dump()<<std::endl;
        return accepted;
    });
    app_->register_message_handler(vsomeip::ANY_SERVICE,vsomeip::ANY_INSTANCE,vsomeip::ANY_METHOD,std::move(handler));
    thread_=std::thread([app=app_]{app->start();});
    std::cout<<Json{{"operation","application.start"},{"application_name",name},
        {"application_id",id},{"message","具名原生 application 已初始化"}}.dump()<<std::endl;
}
ApplicationStack::~ApplicationStack() { stop(); }
void ApplicationStack::stop() {
    if(!thread_.joinable())return;
    app_->clear_all_handler();app_->stop();thread_.join();
    std::cout<<Json{{"operation","application.stop"},{"application_name",app_->get_name()},
        {"application_id",app_->get_client()},{"message","原生 application 已退出并回收线程"}}.dump()<<std::endl;
}
}
