#pragma once
#include "signal_source.hpp"
#include <set>

namespace agent {
// 完整事件模板与标量路径绑定；布局只取冻结 schema，路径由 nlohmann JSON Pointer 解析。
class EventStimulus {
    struct Binding { Json::json_pointer path; SignalSource source; };
    Json schema_, arguments_;
    std::vector<Binding> bindings_;

    static Json schema_at(const Json &root,const Json::json_pointer &path) {
        std::vector<std::string> tokens;
        auto remaining=path;
        while(!remaining.empty()) {tokens.push_back(remaining.back());remaining=remaining.parent_pointer();}
        std::reverse(tokens.begin(),tokens.end());
        Json schema=root;
        for(size_t index=0;index<tokens.size();++index) {
            const auto token=tokens[index];
            if(schema.value("type","")=="struct") {
                bool found=false;
                for(const auto &field:schema.at("fields"))if(field.at("name")==token) {
                    auto next=field;schema=std::move(next);found=true;break;
                }
                if(!found)throw std::runtime_error("激励路径不属于冻结事件 schema");
            } else if(schema.value("type","")=="array") {
                if(schema.contains("vsa")) {
                    if(token!=schema.at("vsa").at("payload_name") || ++index>=tokens.size())
                        throw std::runtime_error("VSA 激励只允许现有 payload 元素，不修改数量指示器");
                }
                // 实际存在性和规范数组下标由 JSON Pointer 的 at() 验证，不自写下标转换。
                auto next=schema.at("element");schema=std::move(next);
            } else throw std::runtime_error("激励路径穿过标量，无法解析类型");
        }
        if(schema.value("type","")=="struct" || schema.value("type","")=="array")
            throw std::runtime_error("激励路径必须绑定现有标量，不能替换结构或数组");
        return schema;
    }
public:
    EventStimulus(Json schema,const Json &arguments,const Json &sources):schema_(std::move(schema)) {
        // 编码再解码获得唯一布局的完整模板，排除 Codec 忽略的额外字段。
        arguments_=Codec::decode(schema_,Codec::encode(schema_,arguments));
        if(!sources.is_array() || sources.size()>128)throw std::runtime_error("完整事件最多绑定 128 个激励源");
        std::set<std::string> paths;
        for(const auto &binding:sources) {
            if(!binding.is_object() || binding.size()!=2 || !binding.contains("path") || !binding.contains("generator"))
                throw std::runtime_error("激励绑定只允许 path 与 generator");
            auto text=binding.at("path").get<std::string>();
            if(text.size()>512)throw std::runtime_error("激励参数路径超过 512 字节");
            Json::json_pointer path(text);
            arguments_.at(path); // 禁止自动扩展数组或凭空创建字段。
            if(!paths.insert(path.to_string()).second)throw std::runtime_error("同一参数路径不能重复绑定");
            auto schema=schema_at(schema_,path);
            const auto &config=binding.at("generator");
            if(!config.is_object())throw std::runtime_error("激励源配置必须为字典");
            static const std::set<std::string> fields={"kind","initial","minimum","maximum","period_seconds","sequence","seed","step_at_ms","step_value","timeline"};
            for(const auto &[key,value]:config.items())if(!fields.count(key))
                throw std::runtime_error("完整事件激励源包含未知字段");
            SignalSource source(schema,config);
            arguments_.at(path)=source.initial();
            bindings_.push_back({std::move(path),std::move(source)});
        }
        Codec::encode(schema_,arguments_); // 组合后的完整布局在替换旧任务前再次验证。
        auto preview=*this;preview.sample_ms(0,0); // 首样本预检使用源副本，不消耗正式随机流。
    }
    Bytes sample(double elapsed,uint64_t index) {
        for(auto &binding:bindings_)arguments_.at(binding.path)=binding.source.value(elapsed,index);
        return Codec::encode(schema_,arguments_);
    }
    Bytes sample_ms(uint64_t elapsed_ms,uint64_t index) {
        for(auto &binding:bindings_)arguments_.at(binding.path)=binding.source.value_ms(elapsed_ms,index);
        return Codec::encode(schema_,arguments_);
    }
    size_t source_count() const { return bindings_.size(); }
    const Json &arguments() const { return arguments_; }
};

// 监控仅取最多 128 个数值叶节点；原始完整 payload 保持不变，截断不冒充完整信号表。
inline Json event_signal_values(const Json &value,bool &truncated) {
    Json result=Json::object();truncated=false;
    std::function<void(const Json &,Json::json_pointer,size_t)> visit;
    visit=[&](const Json &node,Json::json_pointer path,size_t depth) {
        if(result.size()>=128 || depth>64) {truncated=true;return;}
        if(node.is_number() || node.is_boolean())result[path.to_string()]=node;
        else if(node.is_object() || node.is_array()) {
            for(const auto &[key,child]:node.items()) {
                auto next=path;next.push_back(key);visit(child,std::move(next),depth+1);
                if(truncated)return;
            }
        }
    };
    visit(value,Json::json_pointer(""),0);return result;
}
}
