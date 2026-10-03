#pragma once
#include "codec.hpp"
#include <algorithm>
#include <functional>
#include <map>
#include <regex>
#include <set>

namespace agent {
// 确定性时间状态图：启动时编译一次，采样只用标准库二分/模运算，不追赶循环或建立线程。
class TimeStateMachine {
    std::vector<uint64_t> starts_;
    std::vector<Json> values_;
    std::vector<std::string> names_;
    uint64_t cycle_start_=0,cycle_duration_=0;
    size_t index(uint64_t elapsed) const {
        if(cycle_duration_ && elapsed>=cycle_start_)
            elapsed=cycle_start_+(elapsed-cycle_start_)%cycle_duration_;
        return std::upper_bound(starts_.begin(),starts_.end(),elapsed)-starts_.begin()-1;
    }
public:
    TimeStateMachine(const Json &config,const std::function<Json(Json)> &normalize) {
        const auto &states=config.at("states");
        if(!states.is_array() || states.empty() || states.size()>128)
            throw std::runtime_error("时间状态机需要 1–128 个状态");
        std::map<std::string,Json> mapping;
        static const std::regex pattern("^[A-Za-z][A-Za-z0-9_-]{0,63}$");
        static const std::set<std::string> fields={"name","value","duration_ms","next"};
        for(const auto &state:states) {
            if(!state.is_object() || !state.contains("name") || !state.contains("value"))
                throw std::runtime_error("状态必须有 name 与 value");
            for(const auto &[key,value]:state.items())if(!fields.count(key))
                throw std::runtime_error("时间状态包含未知字段");
            auto name=state.at("name").get<std::string>();
            if(!std::regex_match(name,pattern) || mapping.count(name))
                throw std::runtime_error("状态名无效或重复");
            auto checked=state;checked["value"]=normalize(state.at("value"));
            mapping.emplace(name,std::move(checked));
        }
        for(const auto &[name,state]:mapping) {
            if(!state.contains("next") || state.at("next").is_null()) {
                if(state.contains("duration_ms") && !state.at("duration_ms").is_null())
                    throw std::runtime_error("终态不能设置持续时间");
            } else {
                auto next=state.at("next").get<std::string>();
                const auto &duration=state.at("duration_ms");
                if(!mapping.count(next) || !duration.is_number_integer() ||
                    (!duration.is_number_unsigned() && duration.get<int64_t>()<=0) || duration.get<uint64_t>()==0)
                    throw std::runtime_error("转换必须有正整型毫秒持续时间与存在的下一状态");
            }
        }
        auto name=config.at("initial_state").get<std::string>();
        if(!mapping.count(name))throw std::runtime_error("初始状态不存在");
        std::map<std::string,size_t> seen;
        uint64_t elapsed=0;
        bool terminal=false;
        while(!seen.count(name)) {
            seen[name]=starts_.size();starts_.push_back(elapsed);names_.push_back(name);
            const auto &state=mapping.at(name);values_.push_back(state.at("value"));
            if(!state.contains("next") || state.at("next").is_null()) {terminal=true;break;}
            auto duration=state.at("duration_ms").get<uint64_t>();
            if(elapsed>UINT64_MAX-duration)throw std::runtime_error("状态图累计时间超出 uint64 毫秒范围");
            elapsed+=duration;name=state.at("next").get<std::string>();
        }
        if(seen.size()!=mapping.size())throw std::runtime_error("状态图包含从初态不可达的状态");
        if(!terminal) {cycle_start_=starts_.at(seen.at(name));cycle_duration_=elapsed-cycle_start_;}
    }
    const Json &value(uint64_t elapsed) const {return values_.at(index(elapsed));}
    const std::string &state(uint64_t elapsed) const {return names_.at(index(elapsed));}
};
}
