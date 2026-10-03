#pragma once
#include "codec.hpp"
#include <algorithm>
#include <random>

namespace agent {
// 业务激励源，不实现协议或定时器；编码复用 Codec，随机复用标准库。
// 常量/序列/整数随机始终保留 JSON 整数，不经过 double 中间值。
class SignalSource {
    Json schema_, initial_, minimum_, maximum_;
    std::vector<Json> sequence_;
    std::string type_, kind_;
    double period_;
    std::mt19937_64 random_;

    bool integer_type() const { return type_.rfind("int",0)==0 || type_.rfind("uint",0)==0; }
    bool unsigned_type() const { return type_.rfind("uint",0)==0; }
    static double finite_number(const Json &value) {
        if(!value.is_number())throw std::runtime_error("激励参数必须为数值");
        auto result=value.get<double>();
        if(!std::isfinite(result))throw std::runtime_error("激励参数必须为有限值");
        return result;
    }
    static uint64_t seed(const Json &value) {
        if(!value.is_number_integer() || (!value.is_number_unsigned() && value.get<int64_t>()<0))
            throw std::runtime_error("激励种子必须为 uint64 整数");
        return value.get<uint64_t>();
    }
    Json normalize(Json value,bool wave=false) const {
        if(integer_type()) {
            if(value.is_number_float()) {
                auto number=finite_number(value);
                // 旧配置中精确安全范围内的 1.0 可以迁移；不接受已失真大数或隐式截小数。
                if(std::abs(number)>9007199254740991.0 || (!wave && std::trunc(number)!=number))
                    throw std::runtime_error("整数激励不能使用有损浮点值");
                if(unsigned_type()) {
                    if(number<0)throw std::runtime_error("无符号整数激励不能为负数");
                    value=static_cast<uint64_t>(std::trunc(number));
                } else value=static_cast<int64_t>(std::trunc(number));
            }
        } else if(type_=="boolean") {
            if(!value.is_boolean())value=finite_number(value)!=0;
        } else if(type_=="string" && value.is_number()) {
            // 旧标量发生器的数值转文本兼容；完整事件可直接提供原字符串。
            value=std::to_string(finite_number(value));
        } else if(type_=="bytes" && value.is_number()) {
            value=hex(Bytes{static_cast<uint8_t>(std::clamp(finite_number(value),0.0,255.0))});
        }
        auto encoded=Codec::encode(schema_,value);
        if(type_=="float32" || type_=="float64")return Codec::decode(schema_,encoded);
        return value;
    }
public:
    SignalSource(Json schema,const Json &config):schema_(std::move(schema)),
        type_(schema_.value("type","")),kind_(config.value("kind","constant")),
        period_(finite_number(config.value("period_seconds",Json(5)))),
        random_(seed(config.value("seed",Json(0)))) {
        if(kind_!="constant" && kind_!="sequence" && kind_!="random" && kind_!="sine" && kind_!="ramp")
            throw std::runtime_error("未知激励源类型");
        if(type_=="struct" || type_=="array" || type_.empty())
            throw std::runtime_error("单个激励源必须绑定明确标量类型");
        if(period_<=0)throw std::runtime_error("激励周期必须为正数");
        initial_=normalize(config.value("initial",Json(0)));
        if(kind_=="sequence") {
            const auto &values=config.value("sequence",Json::array());
            if(!values.is_array() || values.size()>8192)
                throw std::runtime_error("激励序列必须为最多 8192 项的数组");
            for(const auto &value:values)sequence_.push_back(normalize(value));
        }
        if(kind_=="random" || kind_=="sine" || kind_=="ramp") {
            auto low=config.value("minimum",Json(0)),high=config.value("maximum",Json(100));
            if(!low.is_number() || !high.is_number())
                throw std::runtime_error("激励范围必须为升序数值");
            if(integer_type()) {
                minimum_=normalize(low);maximum_=normalize(high);
                bool reversed=unsigned_type()?maximum_.get<uint64_t>()<minimum_.get<uint64_t>():
                    maximum_.get<int64_t>()<minimum_.get<int64_t>();
                if(reversed)throw std::runtime_error("激励范围必须为升序数值");
            } else {
                auto lo=finite_number(low),hi=finite_number(high);
                if(hi<lo || (kind_=="random" && !std::isfinite(hi-lo)))
                    throw std::runtime_error("随机范围倒置或浮点跨度溢出");
                normalize(low);normalize(high);
                minimum_=low;maximum_=high;
            }
            if((kind_=="sine" || kind_=="ramp") && integer_type()) {
                auto safe=[](const Json &value) {
                    if(value.is_number_unsigned())return value.get<uint64_t>()<=9007199254740991ULL;
                    auto number=value.get<int64_t>();
                    return number>=-9007199254740991LL && number<=9007199254740991LL;
                };
                if(!safe(minimum_) || !safe(maximum_))
                    throw std::runtime_error("整数正弦/斜坡范围超过精确浮点计算边界，请用常量/序列/整数随机");
            }
        }
    }
    const Json &initial() const { return initial_; }
    Json value(double elapsed,uint64_t index) {
        if(!std::isfinite(elapsed) || elapsed<0)throw std::runtime_error("激励时间必须为非负有限值");
        if(kind_=="constant" || (kind_=="sequence" && sequence_.empty()))return initial_;
        if(kind_=="sequence")return sequence_[index%sequence_.size()];
        if(kind_=="random") {
            if(integer_type()) {
                if(unsigned_type())return std::uniform_int_distribution<uint64_t>(
                    minimum_.get<uint64_t>(),maximum_.get<uint64_t>())(random_);
                return std::uniform_int_distribution<int64_t>(
                    minimum_.get<int64_t>(),maximum_.get<int64_t>())(random_);
            }
            // 非整数范围保留原数值行为，实际类型转换及容量校验仍由 Codec 完成。
            return normalize(std::uniform_real_distribution<double>(
                finite_number(minimum_),finite_number(maximum_))(random_));
        }
        double low=finite_number(minimum_),high=finite_number(maximum_);
        // 先归一化相位，避免超大 elapsed/极小 period 溢出后生成 NaN。
        auto phase=std::fmod(elapsed,period_)/period_;
        auto weight=kind_=="sine"?(1+std::sin(2*3.141592653589793*phase))/2:phase;
        auto output=low*(1-weight)+high*weight;
        return normalize(std::clamp(output,low,high),true);
    }
};
}
