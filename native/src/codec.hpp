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
    static void length_prefix(Bytes &data,size_t length,const Json &schema,bool little) {
        auto width=schema.value("length_bytes",4);
        if(width!=1 && width!=2 && width!=4 && width!=8)throw std::runtime_error("长度字段宽度非法");
        if(width<8 && length>=(uint64_t(1)<<(width*8)))throw std::runtime_error("长度字段容量不足");
        integer(data,length,width,little);
    }
public:
    static void encode_one(Bytes &data, const Json &schema, const Json &value) {
        const std::string type = schema.value("type", "struct");
        bool little = schema.value("byte_order", "big") == "little";
        if (type == "struct") {
            if (!value.is_object()) throw std::runtime_error("结构体参数必须为字典");
            for (const auto &field : schema.at("fields"))
                encode_one(data, field, value.at(field.at("name").get<std::string>()));
        } else if (type == "array") {
            if (!value.is_array()) throw std::runtime_error("数组参数必须为列表");
            Bytes body;
            for (const auto &item : value) encode_one(body, schema.at("element"), item);
            if (schema.contains("length")) {
                if (value.size() != number(schema["length"])) throw std::runtime_error("定长数组长度错误");
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
    }
    static Json decode_one(const Bytes &data, size_t &offset, const Json &schema) {
        const std::string type = schema.value("type", "struct");
        bool little = schema.value("byte_order", "big") == "little";
        if (type == "struct") {
            Json out = Json::object();
            for (const auto &field : schema.at("fields"))
                out[field.at("name").get<std::string>()] = decode_one(data, offset, field);
            return out;
        }
        if (type == "array") {
            Json out = Json::array();
            if (schema.contains("length")) {
                auto count = number(schema["length"]);
                if (count > max_frame) throw std::runtime_error("数组长度超限");
                for (uint32_t i=0; i<count; ++i) out.push_back(decode_one(data,offset,schema.at("element")));
            } else {
                size_t length = integer(data,offset,schema.value("length_bytes",4),little);
                size_t end = offset + length;
                if (end > data.size()) throw std::runtime_error("数组截断");
                while (offset < end) {
                    size_t before = offset;
                    out.push_back(decode_one(data,offset,schema.at("element")));
                    if (offset == before || offset > end) throw std::runtime_error("数组元素长度非法");
                }
            }
            return out;
        }
        if (type == "string" || type == "bytes") {
            size_t len = integer(data,offset,schema.value("length_bytes",4),little);
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
    static Bytes encode(const Json &schema, const Json &value) {
        Bytes data; encode_one(data,schema,value);
        if (data.size()>max_frame) throw std::runtime_error("payload 超限");
        return data;
    }
    static Json decode(const Json &schema, const Bytes &data) {
        size_t offset=0; auto value=decode_one(data,offset,schema);
        if (offset!=data.size()) throw std::runtime_error("payload 存在尾随数据");
        return value;
    }
};
}
