#pragma once
#include "ipc.hpp"
#include <cmath>
#include <cstring>
#include <limits>
#include <vector>

namespace agent {
using Bytes = std::vector<uint8_t>;
inline uint32_t number(const Json &value) {
    uint64_t result;
    if(value.is_string()) {
        auto text=value.get<std::string>();size_t end=0;
        if(text.empty() || text[0]=='-')throw std::runtime_error("标识符不能为负数或空字符串");
        result=std::stoull(text,&end,0);
        if(end!=text.size())throw std::runtime_error("标识符包含非法字符");
    } else {
        if(!value.is_number_integer() || (!value.is_number_unsigned() && value.get<int64_t>()<0))
            throw std::runtime_error("标识符必须为非负整数");
        result=value.get<uint64_t>();
    }
    if(result>UINT32_MAX)throw std::runtime_error("标识符超过 uint32 范围");
    return static_cast<uint32_t>(result);
}
inline uint32_t bounded_number(const Json &value,uint32_t maximum,const char *label) {
    auto result=number(value);
    if(result>maximum)throw std::runtime_error(std::string(label)+" 超出范围");
    return result;
}
inline std::string hex(const Bytes &data) {
    std::ostringstream out;
    for (auto byte : data) out << std::hex << std::setw(2) << std::setfill('0') << int(byte);
    return out.str();
}
inline Bytes unhex(const std::string &text) {
    if (text.size() % 2 || text.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos)
        throw std::runtime_error("payload_hex 非法");
    Bytes out;
    for (size_t i = 0; i < text.size(); i += 2) out.push_back(std::stoul(text.substr(i,2), nullptr,16));
    return out;
}
class Codec {
    static uint32_t vsa_capacity(const Json &schema) {
        const auto &vsa=schema.at("vsa");
        const auto size_name=vsa.at("size_name").get<std::string>();
        const auto payload_name=vsa.at("payload_name").get<std::string>();
        const auto size_type=vsa.at("size_type").get<std::string>();
        if(vsa.at("profile")!="VSA_LINEAR" || size_name.empty() || payload_name.empty()
           || size_name==payload_name || schema.contains("length") || !schema.contains("max_length"))
            throw std::runtime_error("VSA_LINEAR 元数据非法或不是有界变长数组");
        uint32_t capacity;
        if(size_type=="uint8")capacity=UINT8_MAX;
        else if(size_type=="uint16")capacity=UINT16_MAX;
        else if(size_type=="uint32")capacity=UINT32_MAX;
        else throw std::runtime_error("VSA_LINEAR size indicator 类型不支持");
        bounded_number(schema.at("max_length"),capacity,"VSA_LINEAR 数量上界");
        return capacity;
    }
    static bool variable(const Json &schema) {
        const std::string type=schema.value("type","struct");
        if(type=="string" || type=="bytes")return true;
        if(type=="array")return !schema.contains("length") || variable(schema.at("element"));
        if(type=="struct")for(const auto &field:schema.at("fields"))if(variable(field))return true;
        return false;
    }
    static size_t padding(const Json &schema,size_t position) {
        auto alignment=bounded_number(schema.value("alignment_bytes",Json(1)),256,"对齐字节数");
        if(!alignment)throw std::runtime_error("对齐字节数必须为正数");
        return (alignment-position%alignment)%alignment;
    }
    static void integer(Bytes &data, uint64_t value, size_t size, bool little) {
        if(size==0 || size>8)throw std::runtime_error("整数/长度字段宽度必须为 1 至 8 字节");
        for (size_t i = 0; i < size; ++i)
            data.push_back((value >> ((little ? i : size - i - 1) * 8)) & 0xff);
    }
    static uint64_t integer(const Bytes &data, size_t &offset, size_t size, bool little) {
        if(size==0 || size>8)throw std::runtime_error("整数/长度字段宽度必须为 1 至 8 字节");
        if (offset + size > data.size()) throw std::runtime_error("payload 截断");
        uint64_t value = 0;
        for (size_t i = 0; i < size; ++i)
            value |= uint64_t(data[offset++]) << ((little ? i : size - i - 1) * 8);
        return value;
    }
    static size_t length_width(const Json &schema) {
        auto width=schema.value("length_bytes",4);
        if(width!=1 && width!=2 && width!=4 && width!=8)throw std::runtime_error("长度字段宽度非法");
        return width;
    }
    static void length_prefix(Bytes &data,size_t length,const Json &schema,bool little) {
        auto width=length_width(schema);
        if(width<8 && length>=(uint64_t(1)<<(width*8)))throw std::runtime_error("长度字段容量不足");
        integer(data,length,width,little);
    }
public:
    static void encode_one(Bytes &data, const Json &schema, const Json &value,
                           size_t message_offset=16,bool last=true) {
        const std::string type = schema.value("type", "struct");
        bool little = schema.value("byte_order", "big") == "little";
        if (type == "struct") {
            if (!value.is_object()) throw std::runtime_error("结构体参数必须为字典");
            Bytes body;
            const auto &fields=schema.at("fields");
            auto prefix=schema.value("length_bytes",0)==0?0:length_width(schema);
            auto body_offset=message_offset+data.size()+prefix;
            for(size_t i=0;i<fields.size();++i) {
                const auto &field=fields[i];
                encode_one(body,field,value.at(field.at("name").get<std::string>()),
                           body_offset,last && i+1==fields.size());
            }
            if(schema.value("length_bytes",0)!=0)length_prefix(data,body.size(),schema,little);
            data.insert(data.end(),body.begin(),body.end());
        } else if (type == "array") {
            const Json *items=&value;
            if(schema.contains("vsa")) {
                auto capacity=vsa_capacity(schema);
                if(!value.is_object())throw std::runtime_error("VSA_LINEAR 参数必须保留源字典字段");
                const auto &vsa=schema.at("vsa");
                items=&value.at(vsa.at("payload_name").get<std::string>());
                const auto &indicator=value.at(vsa.at("size_name").get<std::string>());
                if(!indicator.is_number_integer())throw std::runtime_error("VSA_LINEAR 有效数量必须为整数");
                auto count=bounded_number(indicator,capacity,"VSA_LINEAR 有效数量");
                if(count!=items->size())throw std::runtime_error("VSA_LINEAR 有效数量与 payload 元素数量不一致");
            }
            if (!items->is_array()) throw std::runtime_error("数组参数必须为列表");
            // 实际元素预算与源声明上界分离；错误定长值在编码循环前拒绝。
            if(items->size()>max_frame)throw std::runtime_error("实际数组元素数量超过运行预算");
            if(schema.contains("length") && items->size()!=number(schema["length"]))
                throw std::runtime_error("定长数组长度错误");
            if(schema.contains("max_length") && items->size()>number(schema["max_length"]))
                throw std::runtime_error("变长数组元素数量超限");
            Bytes body;
            auto prefix=schema.contains("length") && schema.value("length_bytes",0)==0?0:length_width(schema);
            auto body_offset=message_offset+data.size()+prefix;
            for(size_t i=0;i<items->size();++i)
                encode_one(body,schema.at("element"),(*items)[i],body_offset,last && i+1==items->size());
            if (schema.contains("length")) {
                if(schema.value("length_bytes",0)!=0)length_prefix(data,body.size(),schema,little);
            } else length_prefix(data,body.size(),schema,little);
            data.insert(data.end(), body.begin(), body.end());
        } else if (type == "string" || type == "bytes") {
            Bytes body;
            if (type == "string") { auto s = value.get<std::string>(); body.assign(s.begin(), s.end()); }
            else body = unhex(value.get<std::string>());
            length_prefix(data,body.size(),schema,little);
            data.insert(data.end(), body.begin(), body.end());
        } else if (type == "boolean") {
            integer(data, value.is_boolean() ? value.get<bool>() : value.get<int>() != 0, 1, little);
        } else if (type == "float32" || type == "float64") {
            double v = value.get<double>();
            if (!std::isfinite(v)) throw std::runtime_error("浮点参数不是有限值");
            if (type == "float32") {
                float f = static_cast<float>(v); uint32_t bits; std::memcpy(&bits, &f, 4);
                if (!std::isfinite(f)) throw std::runtime_error("float32 超限");
                integer(data, bits, 4, little);
            } else { uint64_t bits; std::memcpy(&bits, &v, 8); integer(data, bits, 8, little); }
        } else if (type.rfind("uint", 0) == 0 || type.rfind("int", 0) == 0) {
            bool signed_type = type[0] == 'i';
            size_t bits = std::stoul(type.substr(signed_type ? 3 : 4));
            if (bits != 8 && bits != 16 && bits != 32 && bits != 64) throw std::runtime_error("整数类型非法");
            if (!value.is_number_integer()) throw std::runtime_error("整数参数类型错误");
            uint64_t v;
            if (signed_type) {
                if (value.is_number_unsigned() && value.get<uint64_t>() > uint64_t(INT64_MAX))
                    throw std::runtime_error("有符号整数超限");
                int64_t s = value.get<int64_t>();
                if (bits < 64 && (s < -(int64_t(1) << (bits-1)) || s >= (int64_t(1) << (bits-1))))
                    throw std::runtime_error("有符号整数超限");
                v = static_cast<uint64_t>(s);
            } else {
                if (!value.is_number_unsigned() && value.get<int64_t>() < 0) throw std::runtime_error("无符号整数为负");
                v = value.get<uint64_t>();
                if (bits < 64 && v >= (uint64_t(1) << bits)) throw std::runtime_error("无符号整数超限");
            }
            integer(data, v, bits/8, little);
        } else throw std::runtime_error("不支持的 payload 类型: " + type);
        // 从整条 SOME/IP 消息起点计算；固定元素和全消息末尾不得自动补齐。
        if(!last && variable(schema))data.insert(data.end(),padding(schema,message_offset+data.size()),0);
        if(data.size()>max_frame)throw std::runtime_error("payload 超限");
    }
    static Json decode_body(const Bytes &data, size_t &offset, const Json &schema,bool last) {
        const std::string type = schema.value("type", "struct");
        bool little = schema.value("byte_order", "big") == "little";
        if (type == "struct") {
            size_t end=data.size();
            bool prefixed=schema.value("length_bytes",0)!=0;
            if(prefixed) {
                auto length=integer(data,offset,length_width(schema),little);
                if(length>data.size()-offset)throw std::runtime_error("结构体长度字段超出 payload");
                end=offset+length;
            }
            Json out = Json::object();
            const auto &fields=schema.at("fields");
            for(size_t i=0;i<fields.size();++i) {
                const auto &field=fields[i];
                out[field.at("name").get<std::string>()]=decode_one(data,offset,field,last && i+1==fields.size());
            }
            if(prefixed && offset!=end)throw std::runtime_error("结构体长度字段与字段布局不一致");
            return out;
        }
        if (type == "array") {
            if(schema.contains("vsa"))vsa_capacity(schema);
            Json out = Json::array();
            if (schema.contains("length")) {
                bool prefixed=schema.value("length_bytes",0)!=0;
                size_t end=data.size();
                if(prefixed) {
                    auto length=integer(data,offset,length_width(schema),little);
                    if(length>data.size()-offset)throw std::runtime_error("定长数组长度字段超出 payload");
                    end=offset+length;
                }
                auto count = number(schema["length"]);
                if (count > max_frame) throw std::runtime_error("数组长度超限");
                for (uint32_t i=0; i<count; ++i)
                    out.push_back(decode_one(data,offset,schema.at("element"),last && i+1==count));
                if(prefixed && offset!=end)throw std::runtime_error("定长数组长度字段与元素布局不一致");
            } else {
                size_t length = integer(data,offset,length_width(schema),little);
                if(length>data.size()-offset)throw std::runtime_error("数组截断");
                size_t end = offset + length;
                while (offset < end) {
                    if(schema.contains("max_length") && out.size()>=number(schema["max_length"]))
                        throw std::runtime_error("变长数组元素数量超限");
                    size_t before = offset;
                    // 变长数组由长度字段界定；真正位于消息末尾的元素不消耗尾部 padding。
                    out.push_back(decode_one(data,offset,schema.at("element"),false));
                    if (offset == before || offset > end) throw std::runtime_error("数组元素长度非法");
                }
            }
            if(schema.contains("vsa")) {
                const auto &vsa=schema.at("vsa");
                return Json{{vsa.at("size_name").get<std::string>(),out.size()},
                            {vsa.at("payload_name").get<std::string>(),std::move(out)}};
            }
            return out;
        }
        if (type == "string" || type == "bytes") {
            size_t len = integer(data,offset,length_width(schema),little);
            if (offset + len > data.size()) throw std::runtime_error("字符串/字节串截断");
            Bytes body(data.begin()+offset,data.begin()+offset+len); offset += len;
            if (type == "bytes") return hex(body);
            return std::string(body.begin(),body.end());
        }
        if (type == "boolean") {
            auto value=integer(data,offset,1,little);
            if(value>1)throw std::runtime_error("布尔值必须为 0 或 1");
            return value!=0;
        }
        if (type == "float32") { uint32_t v=integer(data,offset,4,little); float f; std::memcpy(&f,&v,4); return f; }
        if (type == "float64") { uint64_t v=integer(data,offset,8,little); double f; std::memcpy(&f,&v,8); return f; }
        if (type.rfind("uint",0)==0 || type.rfind("int",0)==0) {
            bool signed_type=type[0]=='i'; size_t bits=std::stoul(type.substr(signed_type?3:4));
            if (bits!=8 && bits!=16 && bits!=32 && bits!=64) throw std::runtime_error("整数类型非法");
            uint64_t value=integer(data,offset,bits/8,little);
            if (!signed_type) return value;
            if (bits<64 && (value & (uint64_t(1) << (bits-1)))) value |= ~((uint64_t(1)<<bits)-1);
            return static_cast<int64_t>(value);
        }
        throw std::runtime_error("不支持的 payload 类型: "+type);
    }
    static Json decode_one(const Bytes &data,size_t &offset,const Json &schema,bool last=true) {
        auto value=decode_body(data,offset,schema,last);
        if(!last && variable(schema) && offset<data.size()) {
            auto count=padding(schema,16+offset);
            if(count>data.size()-offset)throw std::runtime_error("对齐 padding 截断");
            offset+=count;
        }
        return value;
    }
    static Bytes encode(const Json &schema, const Json &value) {
        Bytes data; encode_one(data,schema,value);
        if (data.size()>max_frame) throw std::runtime_error("payload 超限");
        return data;
    }
    static Json decode(const Json &schema, const Bytes &data) {
        if(data.size()>max_frame)throw std::runtime_error("payload 超限");
        size_t offset=0; auto value=decode_one(data,offset,schema);
        if (offset!=data.size()) throw std::runtime_error("payload 存在尾随数据");
        return value;
    }
};
}
