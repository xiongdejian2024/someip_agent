#include "runtime.hpp"
#include <random>

namespace agent {
namespace {
Json service_status(const std::shared_ptr<Member> &member, const std::string &state) {
    Json no_return=Json::array();
    for(const auto &[name,api]:member->apis)
        if(!api.event && api.fire_and_forget)no_return.push_back(name);
    return {{"action","event"},{"function","ServiceStatus"},
        {"args",Json{{"state",state},{"instance",member->name},
            {"no_return_methods",no_return},{"service_id",member->service},
            {"instance_id",member->instance},{"subscriptions",member->subscriptions},
            {"application_name",member->application->get_name()},
            {"application_id",member->application->get_client()}}.dump()}};
}
}
void Member::emit(const Json &message) {
    connections.erase(std::remove_if(connections.begin(),connections.end(),[](const auto &c){return c.expired();}),connections.end());
    for (auto &weak : connections) if (auto conn=weak.lock()) conn->send(message);
}
Runtime::Runtime(boost::asio::io_context &io,Json catalog,Json config,std::string bind,std::string name)
    : io_(io),catalog_(std::move(catalog)),bind_(std::move(bind)),default_application_(std::move(name)) {
    std::set<uint16_t> ids;
    if(!config.at("applications").is_array())throw std::runtime_error("applications 必须为具名配置数组");
    for(const auto &entry:config.at("applications")) {
        auto app_name=entry.at("name").get<std::string>();
        auto id=bounded_number(entry.at("id"),0xFFFE,"application id");
        if(app_name.empty() || id==0 || configured_applications_.count(app_name) || !ids.insert(id).second)
            throw std::runtime_error("application 名称或 Client ID 缺失、重复或非法");
        configured_applications_[app_name]=id;
    }
    if(config.at("routing")!=default_application_)
        throw std::runtime_error("默认 application 必须与本进程 routing 配置一致");
    for(auto &[name,spec]:catalog_.items()) if(spec.contains("allowed_subscribers")) {
        restricted_=true;
        for(auto &host:spec.at("allowed_subscribers")) allowed_subscribers_.insert(host.get<std::string>());
    }
    application(default_application_);
}
std::shared_ptr<vsomeip::application> Runtime::application(const std::string &name) {
    if(shutting_down_)throw std::runtime_error("原生进程正在退出");
    auto configured=configured_applications_.find(name);
    if(configured==configured_applications_.end())throw std::runtime_error("application 未在 vsomeip 配置中声明: "+name);
    auto found=applications_.find(name);
    if(found!=applications_.end())return found->second->application();
    // 包括默认路由 application。停止逻辑成员不销毁上下文，数量有界且会话不复用。
    if(applications_.size()>=16)throw std::runtime_error("单进程最多初始化 16 个 application，请建立独立进程");
    auto stack=std::make_unique<ApplicationStack>(name,configured->second,allowed_subscribers_,restricted_,
        [this,name](auto message){
            receiver_->submit(static_cast<std::size_t>(message->get_payload()->get_length())+16,
                [this,name,message]{receive(name,message);});
        });
    auto app=stack->application();applications_.emplace(name,std::move(stack));return app;
}
Runtime::~Runtime() { shutdown(); }
void Runtime::shutdown() {
    if(shutting_down_)return;
    shutting_down_=true;
    receiver_->stop();
    std::vector<std::string> keys;
    for (auto &[key,m] : members_) keys.push_back(key);
    for (auto &key : keys) stop_member(key);
    for (auto &[id,p] : pending_) p.timer->cancel();
    pending_.clear();
    // 先退出代理，最后退出默认路由宿主，保证代理能完成注销。
    for(auto &[name,stack]:applications_)if(name!=default_application_)stack->stop();
    auto primary=applications_.find(default_application_);
    if(primary!=applications_.end())primary->second->stop();
}
void Runtime::stop_member(const std::string &key) {
    auto it=members_.find(key);
    if (it==members_.end()) return;
    auto m=it->second;
    if(m->synchronized)stop_sync();
    m->active=false;
    ++m->generator_epoch;
    m->signal_source.reset();
    if (m->timer) m->timer->cancel();
    ++m->event_epoch;m->event_running=false;
    m->event_stimulus.reset();
    if(m->event_timer)m->event_timer->cancel();
    if (m->role=="server") {
        m->application->stop_offer_service(m->service,m->instance,m->major,m->minor);
        for (auto &[name,api] : m->apis) if (api.event) {
            m->application->stop_offer_event(m->service,m->instance,api.id);
            for (auto group:api.groups) m->application->unregister_subscription_handler(m->service,m->instance,group);
        }
    } else {
        bool another=false;
        for (auto &[other_key,other]:members_)
            if(other_key!=key && other->application==m->application && other->role=="client" &&
                other->service==m->service && other->instance==m->instance)another=true;
        for (auto &name : m->subscriptions) subscribe(m,name,false);
        if(!another) {
            m->application->release_service(m->service,m->instance);
            m->application->unregister_availability_handler(m->service,m->instance,m->major,m->minor);
        }
        // routing_manager_impl 按 application 的 ClientID 去重注册，不按逻辑成员计数。
        // 只有最后一个逻辑消费者停止，才能释放该 application 的事件注册。
        for (auto &[name,api] : m->apis) if (api.event && !has_other_event_consumer(m,api.id))
            m->application->release_event(m->service,m->instance,api.id);
    }
    for(auto pending=pending_.begin();pending!=pending_.end();) {
        if(pending->second.member==m) {pending->second.timer->cancel();pending=pending_.erase(pending);}
        else ++pending;
    }
    m->emit(service_status(m,"OFFLINE"));
    m->listener->close();
    for (auto &weak:m->connections) if (auto conn=weak.lock()) conn->close();
    members_.erase(it);
    std::cout << Json{{"operation","service.stop"},{"member",m->key},{"role",m->role},
                      {"message","服务成员已停止，共享事件仅在最后一个消费者退出时释放"}}.dump() << std::endl;
}
void Runtime::configure(const std::string &alias, const Json &cfg) {
    std::string service_name=cfg.value("service",alias);
    // SAT 用 Service_1 + role=client 表示第二个客户端。
    if (!catalog_.contains(service_name)) {
        auto pos=service_name.rfind('_');
        if (pos!=std::string::npos && catalog_.contains(service_name.substr(0,pos))) service_name=service_name.substr(0,pos);
    }
    const Json &spec=cfg.contains("definition") ? cfg.at("definition") : catalog_.at(service_name);
    auto m=std::make_shared<Member>();
    m->role=cfg.at("role");
    if (m->role!="client" && m->role!="server") throw std::runtime_error("role 必须为 client/server");
    m->name=cfg.value("name",service_name);
    m->key=alias+"_"+m->role;
    m->service=bounded_number(spec.at("service_id"),0xFFFE,"service_id");
    m->instance=bounded_number(cfg.value("instance_id",spec.value("instance_id",Json(1))),0xFFFE,"instance_id");
    m->major=bounded_number(spec.value("major_version",Json(1)),0xFE,"major_version");
    m->minor=number(spec.value("minor_version",Json(0)));
    m->reliable=cfg.value("transport",spec.value("transport","udp"))=="tcp";
    const auto application_name=cfg.value("application_name",default_application_);
    if(!configured_applications_.count(application_name))
        throw std::runtime_error("application 未在 vsomeip 配置中声明: "+application_name);
    if(members_.count(m->key)) {
        const auto existing=members_.at(m->key);
        if(existing->application->get_name()!=application_name || existing->service!=m->service ||
            existing->instance!=m->instance || existing->major!=m->major || existing->minor!=m->minor ||
            existing->reliable!=m->reliable)
            throw std::runtime_error("运行中的成员身份或部署不能直接改变，请先停止成员");
        return;
    }
    for(const auto &[key,other]:members_)if(other->service==m->service && other->instance==m->instance) {
        if(m->role=="server" && other->role=="server")
            throw std::runtime_error("同一服务/实例只能有一个 server，不能重复提供服务");
        if(m->role=="client" && other->role=="client" && other->application->get_name()==application_name &&
            (m->major!=other->major || m->minor!=other->minor || m->reliable!=other->reliable))
            throw std::runtime_error("共享 application 的客户端服务部署必须一致");
    }
    for (auto &category : {"methods","events"}) {
        auto entries=spec.value(category,Json::object());
        for (auto &[name,entry] : entries.items()) {
            Api api; api.name=name;api.id=bounded_number(entry.at("id"),0xFFFF,"method/event id");api.event=std::string(category)=="events";
            api.field=entry.value("field",false);api.fire_and_forget=entry.value("fire_and_forget",false);
            api.input=entry.value("input",entry.value("schema",api.input));
            api.output=entry.value("output",api.input);
            if (api.event) {
                if (api.id<0x8000) throw std::runtime_error("事件 ID 必须 >= 0x8000");
                for (auto &group:entry.value("eventgroups",Json::array({1}))) api.groups.insert(bounded_number(group,0xFFFF,"eventgroup"));
            } else if (api.id>=0x8000) throw std::runtime_error("方法 ID 必须 < 0x8000");
            m->apis[name]=api;
        }
    }
    if(m->role=="client") {
        if(cfg.contains("subscriptions")) {
            if(!cfg.at("subscriptions").is_array())throw std::runtime_error("subscriptions 必须为事件名称数组");
            for(auto &item:cfg.at("subscriptions")) {
                auto name=item.get<std::string>();
                if(!m->apis.count(name) || !m->apis.at(name).event)throw std::runtime_error("订阅配置包含未知事件: "+name);
                m->subscriptions.insert(name);
            }
        } else for(const auto &[name,api]:m->apis)if(api.event)m->subscriptions.insert(name);
    }
    m->application=application(application_name);
    std::weak_ptr<Member> weak_member=m;
    m->listener=Listener::create(io_,bind_,0,false,[this,weak_member](auto conn){
        auto m=weak_member.lock();if(!m || !m->active){conn->close();return;}
        m->connections.push_back(conn);
        conn->handler=[this,m](const auto &message,auto c){command(m,message,c);};
        conn->send(service_status(m,m->state));
        if(m->state=="START") for(const auto &[name,value]:m->field_values) {
            if(!m->subscriptions.count(name))continue;
            auto initial=value;initial["initial"]=true;
            initial["observation"]="vsomeip_field_cache";
            conn->send(initial);
        }
    });
    members_[m->key]=m;
    const auto reliability=m->reliable ? vsomeip::reliability_type_e::RT_RELIABLE : vsomeip::reliability_type_e::RT_UNRELIABLE;
    for (auto &[name,api]:m->apis) if (api.event) {
        auto type=api.field ? vsomeip::event_type_e::ET_FIELD : vsomeip::event_type_e::ET_EVENT;
        if (m->role=="server") m->application->offer_event(m->service,m->instance,api.id,api.groups,type,
            std::chrono::milliseconds::zero(),false,true,nullptr,reliability);
        else if(!has_other_event_consumer(m,api.id))
            m->application->request_event(m->service,m->instance,api.id,api.groups,type,reliability);
    }
    if (m->role=="server") {
        m->application->offer_service(m->service,m->instance,m->major,m->minor);
        m->state="START";
    } else {
        m->application->register_availability_handler(m->service,m->instance,
            [this,application_name](auto service,auto instance,bool available){
                boost::asio::post(io_,[this,application_name,service,instance,available]{
                    for (auto &[key,member]:members_) if (member->role=="client" && member->active &&
                        member->application->get_name()==application_name &&
                        member->service==service && member->instance==instance) {
                        const std::string next=available ? "START" : "OFFLINE";
                        if(member->state==next)continue;
                        member->state=next;
                        if(!available) {
                            member->field_values.clear();
                            member->pending_initial_fields.clear();
                        }
                        member->emit(service_status(member,member->state));
                        if (available) for (auto &name:member->subscriptions) subscribe(member,name,true);
                    }
                });
            },m->major,m->minor);
        m->application->request_service(m->service,m->instance,m->major,m->minor);
        // 默认自动注册所有事件，兼容 SAT 启动后的 event 断言。
        // 只在可用状态转变时进行初次订阅；再次注册 availability handler 不能为旧成员重复回放。
    }
    std::cout << Json{{"operation","service.start"},{"member",m->key},{"role",m->role},
                      {"application_name",application_name},{"application_id",m->application->get_client()},
                      {"message","服务成员已初始化"}}.dump() << std::endl;
}
bool Runtime::has_other_event_consumer(const std::shared_ptr<Member> &member,uint16_t event) const {
    for(const auto &[key,other]:members_) {
        if(key==member->key || !other->active || other->role!="client" || other->application!=member->application ||
            other->service!=member->service || other->instance!=member->instance)continue;
        for(const auto &[name,api]:other->apis)if(api.event && api.id==event)return true;
    }
    return false;
}
void Runtime::subscribe(std::shared_ptr<Member> m,const std::string &name,bool enable) {
    const auto &api=m->apis.at(name);
    for (auto group:api.groups) {
        if (enable) {
            // SAT 断言需要观察每次实际通知，包括内容相同的周期字段。
            // vsomeip 默认字段过滤会吞掉相同值，使用公开订阅 API 显式关闭去重。
            vsomeip::debounce_filter_t filter;
            filter.interval_=0;
            if(api.field)m->pending_initial_fields.insert(name);
            m->application->subscribe_with_debounce(m->service,m->instance,group,m->major,api.id,filter);
        }
        else {
            m->field_values.erase(name);
            m->pending_initial_fields.erase(name);
            bool another=false;
            for(auto &[key,other]:members_) if(key!=m->key && other->active && other->role=="client" && other->application==m->application &&
                other->service==m->service && other->instance==m->instance)
                for(auto &sub:other->subscriptions) if(other->apis.at(sub).id==api.id)another=true;
            if(!another)m->application->unsubscribe(m->service,m->instance,group,api.id);
        }
    }
}
void Runtime::control(const Json &request,std::shared_ptr<Connection> conn) {
    const std::string function=request.value("function","");
    try {
        auto args=unpack_json(request.value("args",Json()));
        Json result=Json::object();
        if (function=="start_config" || function=="start_config_get_args") {
            if (!args.is_object()) throw std::runtime_error("启动配置必须为服务字典");
            if (function=="start_config") {
                std::vector<std::string> stop;
                std::set<std::string> desired;
                for(auto &[alias,cfg]:args.items()) desired.insert(alias+"_"+cfg.at("role").get<std::string>());
                for (auto &[key,m]:members_) if (!desired.count(key)) stop.push_back(key);
                for (auto &key:stop) stop_member(key);
            }
            for (auto &[alias,cfg]:args.items()) {
                std::string key=alias+"_"+cfg.at("role").get<std::string>();
                if (cfg.value("enable","enable")=="disable") {stop_member(key);continue;}
                configure(alias,cfg);
                auto m=members_.at(key);
                Json address=Json::array({bind_,m->listener->port()});
                result[key]=function=="start_config" ? address : Json::array({address,Json{{"apis",catalog_}}});
            }
        } else if (function=="reset") {
            std::vector<std::string> keys; for (auto &[key,m]:members_) keys.push_back(key);
            for (auto &key:keys) stop_member(key);
            result=true;
        } else if (function=="get_current_service_list") {
            result=Json::array();for (auto &[name,spec]:catalog_.items()) result.push_back(name);
        } else if (function=="running_service") {
            for (auto &[key,m]:members_) result[key]={{"state",m->state},{"role",m->role},
                {"application_name",m->application->get_name()},{"application_id",m->application->get_client()},
                {"emitted_count",m->count},{"last_value",m->last_value},
                {"active_state",m->signal_source?m->signal_source->active_state():""},
                {"event_cycle_running",m->event_running},{"event_cycle_count",m->event_count},{"synchronized",m->synchronized},
                {"event_source_count",m->event_stimulus?m->event_stimulus->source_count():0},
                {"event_active_states",m->event_stimulus?m->event_stimulus->active_states():Json::object()},
                {"event_logical_seconds",m->event_logical_ms/1000.0}};
        } else if (function=="monitor") { monitors_.push_back(conn);result=true; }
        else if(function.rfind("event_sync_",0)==0) {result=sync_control(function,args);}
        else if(function=="event_cycle_start" || function=="event_cycle_update") {
            auto m=members_.at(args.at("member").get<std::string>());
            if(m->synchronized)throw std::runtime_error("成员属于同步组，请使用组控制");
            auto name=args.at("function").get<std::string>();
            const auto &api=m->apis.at(name);
            if(m->role!="server" || !api.event)throw std::runtime_error("周期通知需要 server event");
            if(m->signal_source)throw std::runtime_error("旧标量发生器仍在运行，请先明确停止");
            if(function=="event_cycle_update" && !m->event_running)
                throw std::runtime_error("周期通知已停止，不能更新");
            if(m->event_running && m->event_function!=name)
                throw std::runtime_error("此成员已有其他事件周期，不能隐式覆盖");
            auto interval=args.contains("interval_ms")?number(args.at("interval_ms")):m->event_interval.count();
            if(interval<1 || interval>60000)throw std::runtime_error("周期通知间隔必须为 1 至 60000ms");
            // 先验证并编码新 payload，失败不得破坏正在运行的旧周期任务。
            auto payload=Codec::encode(api.input,unpack_json(args.at("args")));
            auto decoded=Codec::decode(api.input,payload);
            auto sources=args.value("sources",Json::array());
            if(!sources.is_array())throw std::runtime_error("事件激励绑定必须为数组");
            std::unique_ptr<EventStimulus> stimulus;
            if(!sources.empty())stimulus=std::make_unique<EventStimulus>(api.input,decoded,sources);
            ++m->event_epoch;
            if(m->event_timer)m->event_timer->cancel();
            m->event_timer=std::make_shared<boost::asio::steady_timer>(io_);
            m->event_function=name;m->event_payload=std::move(payload);
            m->event_arguments=std::move(decoded);
            m->event_stimulus=std::move(stimulus);m->event_logical_ms=0;m->event_sample_index=0;
            m->event_interval=std::chrono::milliseconds(interval);m->event_running=true;
            auto now=std::chrono::steady_clock::now();
            if(function=="event_cycle_start") {m->event_count=0;event_cycle(m,m->event_epoch,now);}
            else {
                auto epoch=m->event_epoch;
                m->event_timer->expires_at(now+m->event_interval);
                m->event_timer->async_wait([this,m,epoch,now](auto error){
                    if(!error)event_cycle(m,epoch,now+m->event_interval);
                });
            }
            result=true;
            std::cout<<Json{{"operation",function},{"member",m->key},{"message","原生周期通知已配置"}}.dump()<<std::endl;
        } else if(function=="event_cycle_stop") {
            auto m=members_.at(args.at("member").get<std::string>());
            if(m->synchronized)throw std::runtime_error("成员属于同步组，请使用组控制");
            ++m->event_epoch;m->event_running=false;
            m->event_stimulus.reset();
            if(m->event_timer)m->event_timer->cancel();
            result=true;
            std::cout<<Json{{"operation",function},{"member",m->key},{"message","原生周期通知已停止"}}.dump()<<std::endl;
        }
        else if (function=="generator_start") {
            auto m=members_.at(args.at("member").get<std::string>());
            if(m->synchronized)throw std::runtime_error("成员属于同步组，请使用组控制");
            if (m->role!="server") throw std::runtime_error("周期发生器仅支持 server");
            if(m->event_running)throw std::runtime_error("完整事件周期仍在运行，请先明确停止");
            const auto &api=m->apis.at(args.at("function").get<std::string>());
            if (!api.event)
                throw std::runtime_error("周期发生器需要 event 接口");
            auto interval=number(args.value("interval_ms",Json(100)));
            if (interval<1 || interval>60000) throw std::runtime_error("周期必须为 1 至 60000ms");
            const auto &source_config=args.at("generator");
            auto signal=source_config.value("signal_name","value");
            if(api.input.value("type","")!="struct" || api.input.at("fields").size()!=1
               || api.input.at("fields").at(0).at("name")!=signal)
                throw std::runtime_error("旧标量发生器需要唯一匹配信号，完整事件请使用完整参数接口");
            const auto &schema=api.input.at("fields").at(0);
            if(source_config.contains("data_type") && source_config.at("data_type")!=schema.at("type"))
                throw std::runtime_error("激励类型与冻结事件 schema 不符");
            auto source=std::make_unique<SignalSource>(schema,source_config);
            // 配置/完整序列先校验；非法更新不能取消原先正常运行的任务。
            Codec::encode(api.input,Json{{signal,source->initial()}});
            if (m->timer) m->timer->cancel();
            m->signal_source=std::move(source);
            m->timer=std::make_shared<boost::asio::steady_timer>(io_);m->count=0;
            auto epoch=++m->generator_epoch;
            // 配置只复制一次，异步 tick 共享不可变快照，不能借用控制请求的生命周期。
            auto cfg=std::make_shared<const Json>(args);
            auto now=std::chrono::steady_clock::now();generator(m,cfg,epoch,now,now);result=true;
        } else if (function=="generator_stop") {
            auto m=members_.at(args.at("member").get<std::string>());
            ++m->generator_epoch;if(m->timer)m->timer->cancel();m->signal_source.reset();result=true;
        } else if (function=="ping") {
            Json declared=Json::object();
            for(const auto &[name,id]:configured_applications_)declared[name]=id;
            Json member_options=Json::object();
            for(const auto &[key,member]:members_) {
                Json options=Json::array();
                for(const auto &weak:member->connections)
                    if(auto connection=weak.lock();connection && connection->socket.is_open())
                        options.push_back(connection->tcp_no_delay());
                member_options[key]=options;
            }
            result={{"runtime","vsomeip"},{"protocol",1},{"version","3.5.10"},
                {"capabilities",Json::array({"member_application_identity_v1","ipc_tcp_no_delay_v1"})},
                {"configured_applications",declared},
                {"ipc_tcp_no_delay",conn->tcp_no_delay()},
                {"member_ipc_tcp_no_delay",member_options},
                {"application_count",applications_.size()},{"pending_requests",pending_.size()}};
        }
        else throw std::runtime_error("未知控制操作: "+function);
        conn->send({{"action","response"},{"function",function},{"result",result.dump()},{"failtype","FAILTYPE_SUCCESS"}});
    } catch (const std::exception &error) {
        log_error("control."+function,error);
        conn->send({{"action","response"},{"function",function},{"result","null"},
                    {"failtype","FAILTYPE_BAD_PARAM"},{"error",error.what()}});
    }
}
void Runtime::trace(std::shared_ptr<Member> m,const Api &api,const Bytes &payload,const std::string &direction,
                    uint8_t type,uint16_t client,uint16_t session,uint8_t code,const Json *decoded,const Json *stimulus_context) {
    // 没有实际监控连接时不构造大型 trace；业务交付仍包含原始 payload_hex。
    monitors_.erase(std::remove_if(monitors_.begin(),monitors_.end(),[](const auto &weak){
        auto connection=weak.lock();return !connection || !connection->socket.is_open();
    }),monitors_.end());
    if(monitors_.empty())return;
    Json message={{"action","trace"},{"function",api.name},{"member",m->key},{"direction",direction},
        {"service_id",m->service},{"instance_id",m->instance},{"method_id",api.id},{"client_id",client},
        {"session_id",session},{"message_type",type},{"return_code",code},{"payload_hex",hex(payload)},
        {"transport",m->reliable?"tcp":"udp"},{"interface_version",m->major},{"emitted_count",m->count},
        {"application_name",m->application->get_name()},{"application_id",m->application->get_client()},
        {"last_value",m->last_value},{"observation","vsomeip_api"},
        {"native_monotonic_ns",std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count()}};
    if(api.event) {
        if(stimulus_context && direction!="rx")for(const auto &[key,value]:stimulus_context->items())message[key]=value;
        if(type==2 && direction!="rx") {
            if(m->signal_source && !m->signal_source->active_state().empty())
                message["active_states"]=Json{{"",m->signal_source->active_state()}};
            else if(m->event_stimulus && api.name==m->event_function)
                message["active_states"]=m->event_stimulus->active_states();
        }
        bool truncated=false;
        message["signal_values"]=decoded?event_signal_values(*decoded,truncated):
            event_signal_values(Codec::decode(api.input,payload),truncated);
        message["signal_values_truncated"]=truncated;
    }
    for (auto &weak:monitors_) if(auto conn=weak.lock())conn->send(message);
}
void Runtime::notify(std::shared_ptr<Member> m,const Api &api,const Bytes &data,const Json *decoded,const Json *stimulus_context) {
    auto payload=vsomeip::runtime::get()->create_payload();payload->set_data(data);
    m->application->notify(m->service,m->instance,api.id,payload,true);
    trace(m,api,data,"tx",2,0,0,0,decoded,stimulus_context);
}
void Runtime::command(std::shared_ptr<Member> m,const Json &request,std::shared_ptr<Connection> conn) {
    const std::string function=request.value("function","");
    try {
        if (!m->active) throw std::runtime_error("服务已停止");
        const std::string action=request.at("action");
        auto args=unpack_json(request.value("args",Json()));
        if (function=="RegistEvent" || function=="UnRegistEvent") {
            if (m->role!="client") throw std::runtime_error("只有 client 可注册事件");
            bool enable=function=="RegistEvent";
            std::set<std::string> names;
            for(auto &item:args.at("event_list")) {
                auto name=item.is_string()?item.get<std::string>():item.begin().key();
                bool matched=false;
                for (auto &[api_name,api]:m->apis)
                    if(api.event && (name=="all" || api_name==name || api_name=="Update"+name+"Event")) {names.insert(api_name);matched=true;}
                if(!matched && name!="all")throw std::runtime_error("未知订阅事件: "+name);
            }
            for(auto &name:names) {
                if(enable)m->subscriptions.insert(name);else m->subscriptions.erase(name);
                subscribe(m,name,enable);
            }
            conn->send({{"action","response"},{"function",function},{"result","true"},{"failtype","FAILTYPE_SUCCESS"},
                {"correlation_id",request.value("correlation_id","")},{"subscriptions",m->subscriptions}});
            return;
        }
        std::string api_name=function;
        if (api_name.size()>5 && api_name.substr(api_name.size()-5)=="Async") api_name.resize(api_name.size()-5);
        const auto &api=m->apis.at(api_name);
        const bool acknowledge=request.value("acknowledge",false);
        if(acknowledge && action=="request" && !api.fire_and_forget)
            throw std::runtime_error("有响应方法不能同时请求本地提交回执，请等待真实方法响应");
        if(action=="event") {
            if(m->role!="server" || !api.event) throw std::runtime_error("通知需要 server event");
            auto data=request.contains("payload_hex")?unhex(request.at("payload_hex")):Codec::encode(api.input,args);
            notify(m,api,data);
        } else if(action=="request") {
            if(m->role!="client" || api.event) throw std::runtime_error("请求需要 client method");
            if(m->state!="START") throw std::runtime_error("服务未上线");
            auto data=request.contains("payload_hex")?unhex(request.at("payload_hex")):Codec::encode(api.input,args);
            auto message=vsomeip::runtime::get()->create_request(m->reliable);
            message->set_service(m->service);message->set_instance(m->instance);message->set_method(api.id);
            message->set_interface_version(m->major);
            if(api.fire_and_forget)message->set_message_type(vsomeip::message_type_e::MT_REQUEST_NO_RETURN);
            auto payload=vsomeip::runtime::get()->create_payload();payload->set_data(data);message->set_payload(payload);
            m->application->send(message);
            trace(m,api,data,"tx",api.fire_and_forget?1:0,message->get_client(),message->get_session());
            if(!api.fire_and_forget) {
                uint32_t id=message->get_request();
                auto timer=std::make_shared<boost::asio::steady_timer>(io_);
                if(!pending_.emplace(id,Pending{conn,m,function,request.value("correlation_id",""),timer}).second)
                    throw std::runtime_error("线上 Request ID 与未完成请求冲突，不能覆盖原请求");
                timer->expires_after(std::chrono::milliseconds(request.value("timeout_ms",5000)));
                timer->async_wait([this,id](auto ec){
                    if(ec)return;
                    auto it=pending_.find(id);if(it==pending_.end())return;
                    if(auto c=it->second.connection.lock())c->send({{"action","response"},{"function",it->second.function},
                        {"result","null"},{"failtype","FAILTYPE_TIMEOUT"},{"request_id",id},
                        {"correlation_id",it->second.correlation}});
                    pending_.erase(it);
                });
            }
        } else if(action=="response") {
            if(m->role!="server")throw std::runtime_error("响应需要 server");
            auto &queue=m->requests[api_name];
            if(queue.empty())throw std::runtime_error("没有待响应请求");
            auto it=queue.begin();
            if(request.contains("request_id")) {
                it=std::find_if(queue.begin(),queue.end(),[&](auto &msg){return msg->get_request()==number(request["request_id"]);});
                if(it==queue.end())throw std::runtime_error("request_id 不存在");
            }
            auto result=unpack_json(request.value("result",Json()));
            if(result.is_object() && result.contains("out"))result=result["out"];
            auto data=request.contains("payload_hex")?unhex(request.at("payload_hex")):Codec::encode(api.output,result);
            const auto return_code=bounded_number(request.value("return_code",Json(0)),0xFF,"return_code");
            const auto response_type=number(request.value("message_type",Json(0x80)));
            if(response_type!=0x80 && response_type!=0x81)throw std::runtime_error("服务响应类型必须为 RESPONSE 或 ERROR");
            auto response=vsomeip::runtime::get()->create_response(*it);
            queue.erase(it);
            auto payload=vsomeip::runtime::get()->create_payload();payload->set_data(data);response->set_payload(payload);
            response->set_return_code(static_cast<vsomeip::return_code_e>(return_code));
            response->set_message_type(static_cast<vsomeip::message_type_e>(response_type));
            m->application->send(response);trace(m,api,data,"tx",response_type,response->get_client(),response->get_session(),return_code);
        } else throw std::runtime_error("未知成员操作: "+action);
        if(acknowledge)conn->send({{"action","response"},{"function",function},
            {"result","true"},{"failtype","FAILTYPE_SUCCESS"},
            {"correlation_id",request.value("correlation_id","")},{"observation","native_submission"}});
    } catch(const std::exception &error) {
        log_error("member."+function,error);
        conn->send({{"action","response"},{"function",function},{"result","null"},
                    {"failtype","FAILTYPE_BAD_PARAM"},{"error",error.what()},
                    {"correlation_id",request.value("correlation_id","")}});
    }
}
void Runtime::receive(const std::string &application_name,std::shared_ptr<vsomeip::message> message) {
    if(shutting_down_)return;
    auto payload=message->get_payload();Bytes data(payload->get_data(),payload->get_data()+payload->get_length());
    uint8_t type=static_cast<uint8_t>(message->get_message_type());
    try {
        if(type==0x80 || type==0x81) {
            auto it=pending_.find(message->get_request());if(it==pending_.end())return;
            auto p=it->second;
            std::string name=p.function;if(name.size()>5 && name.substr(name.size()-5)=="Async")name.resize(name.size()-5);
            auto api=p.member->apis.at(name);
            // Request ID 相同不足以证明匹配；错误信封不得消费真正请求的超时/响应槽。
            if(!p.member->active || p.member->application->get_name()!=application_name ||
                message->get_service()!=p.member->service || message->get_instance()!=p.member->instance ||
                message->get_method()!=api.id || message->get_interface_version()!=p.member->major ||
                message->is_reliable()!=p.member->reliable) {
                std::cout<<Json{{"operation","someip.response.reject"},{"application_name",application_name},
                    {"request_id",message->get_request()},{"message","响应身份或部署与待完成请求不匹配"}}.dump()<<std::endl;
                return;
            }
            p.timer->cancel();pending_.erase(it);
            const auto return_code=static_cast<uint8_t>(message->get_return_code());
            Json delivery={{"action","response"},{"function",p.function},
                {"result",Json{{"out",nullptr}}.dump()},{"failtype","FAILTYPE_OTHER_ERROR"},
                {"request_id",message->get_request()},{"correlation_id",p.correlation},
                {"return_code",return_code},{"message_type",type},{"payload_hex",hex(data)}};
            // ERROR 的 payload 不一定符合正常返回值 schema，空错误报文不能被误当成等待超时。
            if(type==0x80 && return_code==0) {
                try {
                    delivery["result"]=Json{{"out",Codec::decode(api.output,data)}}.dump();
                    delivery["failtype"]="FAILTYPE_SUCCESS";
                } catch(const std::exception &error) {
                    log_error("someip.response.decode",error);
                    delivery["failtype"]="FAILTYPE_DESERIALIZATION_FAILURE";
                    delivery["error"]=error.what();
                }
            } else delivery["error"]="SOME/IP 返回错误，原始 return_code="+std::to_string(return_code);
            if(auto conn=p.connection.lock())conn->send(delivery);
            trace(p.member,api,data,"rx",type,message->get_client(),message->get_session(),static_cast<uint8_t>(message->get_return_code()));
            return;
        }
        for(auto &[key,m]:members_) {
            if(!m->active || m->application->get_name()!=application_name)continue;
            if(m->service!=message->get_service() || m->instance!=message->get_instance())continue;
            if((type==2 && m->role!="client") || ((type==0 || type==1) && m->role!="server"))continue;
            for(auto &[name,api]:m->apis) if(api.id==message->get_method()) {
                if(type==2 && !m->subscriptions.count(name))continue;
                // 同一 vsomeip application 承载多个逻辑客户端。订阅触发的字段初值
                // 只交给本次等待初值的成员，不能污染原成员的事件历史。
                if(type==2 && api.field && message->is_initial() &&
                    !m->pending_initial_fields.count(name))continue;
                auto args=Codec::decode(api.input,data);
                if(type==0)m->requests[name].push_back(message);
                Json delivery={{"action",type==2?"event":"request"},{"function",name},{"args",args.dump()},
                    {"failtype","FAILTYPE_SUCCESS"},{"request_id",message->get_request()},{"payload_hex",hex(data)},
                    {"initial",message->is_initial()}};
                if(type==2 && api.field) {
                    m->pending_initial_fields.erase(name);
                    m->field_values[name]=delivery;
                }
                m->emit(delivery);
                trace(m,api,data,"rx",type,message->get_client(),message->get_session(),0,&args);
                break;
            }
        }
    } catch(const std::exception &error) {log_error("someip.receive",error);}
}
void Runtime::generator(std::shared_ptr<Member> m,std::shared_ptr<const Json> cfg,uint64_t epoch,std::chrono::steady_clock::time_point started,
                        std::chrono::steady_clock::time_point deadline) {
    if(!m->active || m->generator_epoch!=epoch)return;
    auto elapsed_duration=std::chrono::steady_clock::now()-started;
    double elapsed=std::chrono::duration<double>(elapsed_duration).count();
    const auto &g=cfg->at("generator");
    auto &api=m->apis.at(cfg->at("function").get<std::string>());
    std::string signal=g.value("signal_name","value");
    auto scalar=m->signal_source->needs_millisecond_clock()?
        m->signal_source->value_ms(static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(elapsed_duration).count()),m->count):
        m->signal_source->value(elapsed,m->count);
    m->last_value=scalar;++m->count;
    notify(m,api,Codec::encode(api.input,Json{{signal,scalar}}));
    auto interval=std::chrono::milliseconds(cfg->value("interval_ms",100));
    deadline+=interval;
    if(deadline<std::chrono::steady_clock::now())deadline=std::chrono::steady_clock::now()+interval;
    m->timer->expires_at(deadline);
    m->timer->async_wait([this,m,cfg,epoch,started,deadline](auto ec){
        if(ec)return;
        try {generator(m,cfg,epoch,started,deadline);} catch(const std::exception &error){
            log_error("generator.tick",error);
            Json failure={{"action","error"},{"member",m->key},{"operation","generator.tick"},{"error",error.what()}};
            m->emit(failure);
            for(auto &weak:monitors_)if(auto conn=weak.lock())conn->send(failure);
        }
    });
}
void Runtime::event_cycle(std::shared_ptr<Member> m,uint64_t epoch,std::chrono::steady_clock::time_point deadline) {
    if(!m->active || !m->event_running || m->event_epoch!=epoch)return;
    try {
        if(m->event_stimulus)m->event_payload=m->event_stimulus->sample_ms(m->event_logical_ms,m->event_sample_index);
        notify(m,m->apis.at(m->event_function),m->event_payload,
            m->event_stimulus?&m->event_stimulus->arguments():&m->event_arguments);
        ++m->event_count;
        ++m->event_sample_index;
        if(m->event_logical_ms>UINT64_MAX-static_cast<uint64_t>(m->event_interval.count()))
            throw std::runtime_error("事件逻辑时间超出运行预算");
        m->event_logical_ms+=m->event_interval.count();
        deadline+=m->event_interval;
        if(deadline<std::chrono::steady_clock::now())deadline=std::chrono::steady_clock::now()+m->event_interval;
        m->event_timer->expires_at(deadline);
        m->event_timer->async_wait([this,m,epoch,deadline](auto error){
            if(!error)event_cycle(m,epoch,deadline);
        });
    } catch(const std::exception &error) {
        m->event_running=false;
        log_error("event_cycle.tick",error);
        Json failure={{"action","error"},{"member",m->key},{"operation","event_cycle.tick"},{"error",error.what()}};
        m->emit(failure);
        for(auto &weak:monitors_)if(auto connection=weak.lock())connection->send(failure);
    }
}
}
