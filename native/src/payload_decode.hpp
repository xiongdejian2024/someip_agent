#pragma once
#include "codec.hpp"
#include <set>

namespace agent {
// 复用在线调用的 Codec，只添加观测任务的资源与结果契约，不另写 payload 解析器。
inline Json decode_payload_batch(const Json &args) {
    const auto &schema=args.at("schema");
    if(schema.at("type")!="struct" || !schema.at("fields").is_array() || schema.at("fields").size()>100)
        throw std::runtime_error("观测 schema 必须为至多100个参数的结构体");
    std::vector<std::pair<const Json *,size_t>> pending{{&schema,0}};
    size_t nodes=0;
    while(!pending.empty()) {
        auto [node,depth]=pending.back();pending.pop_back();
        if(++nodes>4096 || depth>32)throw std::runtime_error("观测 schema 节点或深度超过预算");
        const auto type=node->at("type").get<std::string>();
        static const std::set<std::string> types={"struct","array","string","bytes","boolean",
            "uint8","uint16","uint32","uint64","int8","int16","int32","int64","float32","float64"};
        if(!types.count(type))throw std::runtime_error("观测 schema 类型不受支持");
        if(node->contains("byte_order") && node->at("byte_order")!="big" && node->at("byte_order")!="little")
            throw std::runtime_error("观测 schema 字节序非法");
        if(node->contains("fields")) {
            if(!node->at("fields").is_array())throw std::runtime_error("schema fields 必须为数组");
            std::set<std::string> names;
            for(const auto &field:node->at("fields")) {
                auto name=field.at("name").get<std::string>();
                if(name.empty() || !names.insert(name).second)throw std::runtime_error("观测 schema 字段名为空或重复");
                pending.emplace_back(&field,depth+1);
            }
        }
        if(node->contains("element"))pending.emplace_back(&node->at("element"),depth+1);
    }
    if(schema.dump().size()>65536)throw std::runtime_error("观测 schema 超过64KiB预算");
    const auto &payloads=args.at("payloads");
    if(!payloads.is_array() || payloads.empty() || payloads.size()>32)
        throw std::runtime_error("解码批次必须包含1至32个payload");
    size_t input_bytes=0,output_bytes=0;
    Json records=Json::array();
    for(const auto &payload:payloads) {
        if(!payload.is_string() || payload.get_ref<const std::string &>().size()>131072)
            throw std::runtime_error("单个观测payload超过64KiB或不是十六进制字符串");
        input_bytes+=payload.get_ref<const std::string &>().size();
    }
    if(input_bytes>524288)throw std::runtime_error("观测批次payload超过256KiB预算");
    for(const auto &payload:payloads) {
        try {
            auto value=Codec::decode(schema,unhex(payload.get<std::string>()));
            std::vector<const Json *> values{&value};
            while(!values.empty()) {
                auto item=values.back();values.pop_back();
                if(item->is_null() || (item->is_number_float() && !std::isfinite(item->get<double>())))
                    throw std::runtime_error("解码值不是有限JSON值");
                if(item->is_structured())for(const auto &child:*item)values.push_back(&child);
            }
            auto size=value.dump().size(); // 同时由成熟JSON库严格核验字符串UTF-8。
            if(size>262144 || output_bytes+size>1048576)
                throw std::runtime_error("观测解码结果超过输出预算");
            output_bytes+=size;
            records.push_back({{"values",std::move(value)}});
        } catch(const std::exception &error) {
            log_error("payload.decode",error);
            records.push_back({{"error",std::string(error.what()).substr(0,2048)}});
        }
    }
    return {{"schema_version",1},{"decoder","someip-agent-native-codec"},{"records",std::move(records)}};
}
}
