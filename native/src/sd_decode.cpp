#include "sd_decode.hpp"
#include <vsomeip/vsomeip.hpp>
#include <implementation/message/include/deserializer.hpp>
#include <implementation/service_discovery/include/constants.hpp>
#include <implementation/service_discovery/include/defines.hpp>
#include <implementation/service_discovery/include/message_impl.hpp>
#include <implementation/service_discovery/include/entry_impl.hpp>
#include <implementation/service_discovery/include/serviceentry_impl.hpp>
#include <implementation/service_discovery/include/eventgroupentry_impl.hpp>
#include <implementation/service_discovery/include/option_impl.hpp>

namespace agent {
Json decode_sd(const uint8_t *data,size_t size) {
    vsomeip::deserializer decoder(0);
    decoder.set_data(data,size);
    vsomeip::sd::message_impl message;
    if(!message.deserialize(&decoder))
        throw std::runtime_error("vsomeip SD 模型无法解码报文");
    if(message.get_client()!=vsomeip::sd::client ||
       message.get_interface_version()!=vsomeip::sd::interface_version ||
       message.get_message_type()!=vsomeip::sd::message_type ||
       message.get_return_code()!=vsomeip::sd::return_code)
        throw std::runtime_error("SD 头属性与固定 vsomeip 协议常量不一致");
    const auto &entries=message.get_entries();
    const auto &options=message.get_options();
    // 上游会容忍缺少 options-length 或丢弃尾随数据；观测端不能将其当完整成功。
    size_t expected=VSOMEIP_SOMEIP_SD_EMPTY_MESSAGE_SIZE+
                    entries.size()*VSOMEIP_SOMEIP_SD_ENTRY_SIZE+message.get_options_length();
    size_t option_size=0;
    for(const auto &option:options) {
        if(!option)throw std::runtime_error("SD 返回空 Option");
        option_size+=option->get_size();
    }
    if(size!=expected || decoder.get_remaining()!=0 || option_size!=message.get_options_length())
        throw std::runtime_error("SD 数组截断、缺少长度字段、Option 解码不完整或包含尾随数据");
    if(entries.size()>4096 || options.size()>4096)
        throw std::runtime_error("SD 展示分析超过 4096 条 Entry/Option 资源上限");
    Json result={{"schema_version",1},{"decoder","vsomeip-3.5.10"},
                 {"flags",data[VSOMEIP_FULL_HEADER_SIZE]},
                 {"entries",Json::array()},{"options",Json::array()}};
    for(const auto &entry:entries) {
        if(!entry)throw std::runtime_error("SD 返回空 Entry");
        Json item={{"entry_type",static_cast<uint8_t>(entry->get_type())},
                   {"service_id",entry->get_service()},{"instance_id",entry->get_instance()},
                   {"major_version",entry->get_major_version()},{"ttl",entry->get_ttl()},
                   {"minor_version",nullptr},{"eventgroup_id",nullptr},{"counter",nullptr},
                   {"option_indices",Json::array()}};
        for(uint8_t run:{uint8_t(1),uint8_t(2)}) {
            const auto &indices=entry->get_options(run);
            if(indices.size()!=entry->get_num_options(run) ||
               (!indices.empty() && size_t(indices.front())+indices.size()>256))
                throw std::runtime_error("SD Option run 数量不一致或索引回绕");
            for(auto index:indices)if(index>=options.size())
                throw std::runtime_error("SD Entry 引用不存在的 Option");
            item["option_indices"].push_back(indices);
        }
        if(auto service=std::dynamic_pointer_cast<vsomeip::sd::serviceentry_impl>(entry))
            item["minor_version"]=service->get_minor_version();
        else if(auto group=std::dynamic_pointer_cast<vsomeip::sd::eventgroupentry_impl>(entry)) {
            // 固定 SDK deserialize 将线上的 Reserved/Counter 放在 reserved_；get_counter 未填充。
            item["eventgroup_id"]=group->get_eventgroup();item["counter"]=group->get_reserved() & 0x0F;
        } else throw std::runtime_error("SD Entry 没有受支持的库模型类型");
        result["entries"].push_back(item);
    }
    for(const auto &option:options)result["options"].push_back({
        {"option_type",static_cast<uint8_t>(option->get_type())},{"length",option->get_length()}
    });
    return result;
}
}
