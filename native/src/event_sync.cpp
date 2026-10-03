#include "runtime.hpp"

namespace agent {
Json Runtime::sync_status() const {
    if(!event_sync_)return {{"active",false},{"paused",false},{"group_id",nullptr},
        {"logical_ms",0},{"frame_index",0},{"interval_ms",nullptr},{"speed",1},{"events",Json::array()},{"last_error",nullptr}};
    const auto &group=*event_sync_;
    Json events=Json::array();
    for(const auto &entry:group.entries)events.push_back({{"member",entry.member->key},{"function",entry.function},
        {"interval_ms",entry.interval_ms},{"emitted_count",entry.count},{"last_logical_ms",entry.last_logical_ms},
        {"source_count",entry.stimulus?entry.stimulus->source_count():0},
        {"active_states",entry.stimulus?entry.stimulus->active_states():Json::object()}});
    return {{"active",group.active},{"paused",group.paused},{"group_id",group.id},
        {"logical_ms",group.clock.logical_ms()},{"frame_index",group.clock.frame()},
        {"interval_ms",group.clock.interval_ms()},{"speed",group.clock.speed()},{"events",events},
        {"last_error",group.error.empty()?Json(nullptr):Json(group.error)}};
}
void Runtime::stop_sync() {
    if(!event_sync_)return;
    auto &group=*event_sync_;
    ++group.epoch;group.active=false;group.paused=false;
    group.timer->cancel();
    for(auto &entry:group.entries) {
        entry.member->synchronized=false;
        entry.stimulus.reset();entry.payload.clear();entry.arguments=Json();
    }
}
Json Runtime::sync_control(const std::string &operation,const Json &args) {
    if(operation=="event_sync_status")return sync_status();
    if(operation=="event_sync_stop") {
        stop_sync();
        std::cout<<Json{{"operation",operation},{"message","原生同步组已停止并释放激励"}}.dump()<<std::endl;
        return sync_status();
    }
    if(operation=="event_sync_start") {
        if(event_sync_ && event_sync_->active)throw std::runtime_error("同步组仍占用成员，请先停止");
        if(!args.is_object() || args.dump().size()>max_frame)
            throw std::runtime_error("同步配置超过 4 MiB 控制消息预算");
        const auto &events=args.at("events");
        if(!events.is_array() || events.empty() || events.size()>16)
            throw std::runtime_error("同步组需要 1 至 16 个事件");
        auto id=args.at("group_id").get<std::string>();
        if(id.empty() || id.size()>64 || id.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")!=std::string::npos)
            throw std::runtime_error("同步组标识非法");
        if(!args.at("paused").is_boolean() || !args.at("speed").is_number())
            throw std::runtime_error("暂停必须为布尔值，倍率必须为数字");
        std::vector<uint64_t> intervals;
        std::vector<SyncEntry> entries;
        std::set<std::pair<std::string,std::string>> identities;
        for(const auto &config:events) {
            auto member=members_.at(config.at("member").get<std::string>());
            auto function=config.at("function").get<std::string>();
            const auto &api=member->apis.at(function);
            if(!member->active || member->role!="server" || !api.event)
                throw std::runtime_error("同步激励需要活动 server event");
            if(member->signal_source || member->event_running)
                throw std::runtime_error("成员仍有独立周期任务，请先明确停止");
            if(!identities.insert({member->key,function}).second)
                throw std::runtime_error("同步组不能重复绑定同一成员事件");
            const auto &interval=config.at("interval_ms");
            if(!interval.is_number_integer() || interval.is_boolean() || interval<1 || interval>60000)
                throw std::runtime_error("同步事件周期必须为 1 至 60000 整型毫秒");
            SyncEntry entry;
            entry.member=member;entry.function=function;entry.interval_ms=interval.get<uint64_t>();
            entry.payload=Codec::encode(api.input,unpack_json(config.at("args")));
            entry.arguments=Codec::decode(api.input,entry.payload);
            const auto sources=config.value("sources",Json::array());
            if(!sources.is_array())throw std::runtime_error("同步事件激励绑定必须为数组");
            if(!sources.empty())entry.stimulus=std::make_unique<EventStimulus>(api.input,entry.arguments,sources);
            intervals.push_back(entry.interval_ms);entries.push_back(std::move(entry));
        }
        // 全部事件预编译完成后才取得所有权，失败不启动部分组或改变已有任务。
        auto group=std::make_shared<SyncGroup>(io_,intervals);
        group->clock.speed(args.at("speed").get<double>());
        group->id=id;group->entries=std::move(entries);group->paused=args.at("paused").get<bool>();
        for(auto &entry:group->entries)entry.member->synchronized=true;
        event_sync_=group;
        if(!group->paused)sync_frame(group,std::chrono::steady_clock::now());
    } else {
        if(!event_sync_ || !event_sync_->active)throw std::runtime_error("没有活动同步组");
        auto group=event_sync_;
        if(operation=="event_sync_pause") {
            ++group->epoch;group->timer->cancel();group->paused=true;
        } else if(operation=="event_sync_resume") {
            if(!group->paused)throw std::runtime_error("同步组已运行，不能重复恢复");
            group->paused=false;sync_frame(group,std::chrono::steady_clock::now());
        } else if(operation=="event_sync_step") {
            if(!group->paused)throw std::runtime_error("单步仅允许已暂停的同步组");
            sync_frame(group,std::chrono::steady_clock::now());
        } else if(operation=="event_sync_speed") {
            if(!args.at("speed").is_number())throw std::runtime_error("倍率必须为数字");
            group->clock.speed(args.at("speed").get<double>());
            ++group->epoch;group->timer->cancel();
            if(!group->paused)arm_sync(group,std::chrono::steady_clock::now()+group->clock.wall_interval());
        } else throw std::runtime_error("未知同步操作");
    }
    std::cout<<Json{{"operation",operation},{"message","原生同步时钟操作完成"}}.dump()<<std::endl;
    return sync_status();
}
void Runtime::arm_sync(const std::shared_ptr<SyncGroup> &group,std::chrono::steady_clock::time_point deadline) {
    auto epoch=group->epoch;
    group->timer->expires_at(deadline);
    group->timer->async_wait([this,group,epoch,deadline](auto error){
        if(!error && event_sync_==group && group->active && !group->paused && group->epoch==epoch)
            sync_frame(group,deadline);
    });
}
void Runtime::sync_frame(const std::shared_ptr<SyncGroup> &group,std::chrono::steady_clock::time_point deadline) {
    if(event_sync_!=group || !group->active)return;
    try {
        group->clock.require_advance();
        // 所有本帧事件先采样／编码，随后按配置顺序提交；网络交付不承诺原子或硬实时。
        for(auto &entry:group->entries)if(group->clock.due(entry.interval_ms)) {
            if(!entry.member->active)throw std::runtime_error("同步成员已释放");
            if(entry.stimulus)entry.payload=entry.stimulus->sample_ms(group->clock.logical_ms(),entry.count);
        }
        for(auto &entry:group->entries)if(group->clock.due(entry.interval_ms)) {
            Json context={{"sync_group_id",group->id},{"sync_logical_ms",group->clock.logical_ms()},
                {"sync_frame_index",group->clock.frame()},
                {"active_states",entry.stimulus?entry.stimulus->active_states():Json::object()}};
            notify(entry.member,entry.member->apis.at(entry.function),entry.payload,
                entry.stimulus?&entry.stimulus->arguments():&entry.arguments,&context);
            entry.last_logical_ms=group->clock.logical_ms();++entry.count;
        }
        group->clock.advance();
        if(group->paused)return;
        deadline+=group->clock.wall_interval();
        if(deadline<std::chrono::steady_clock::now())deadline=std::chrono::steady_clock::now()+group->clock.wall_interval();
        arm_sync(group,deadline); // 迟到减慢，不追赶／跳过业务样本。
    } catch(const std::exception &error) {
        group->error=error.what();stop_sync();log_error("event_sync.tick",error);
        Json failure={{"action","error"},{"operation","event_sync.tick"},{"error",error.what()},{"sync_group_id",group->id}};
        for(auto &entry:group->entries)entry.member->emit(failure);
        for(auto &weak:monitors_)if(auto connection=weak.lock())connection->send(failure);
    }
}
}
