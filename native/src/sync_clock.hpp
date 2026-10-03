#pragma once
#include <chrono>
#include <cstdint>
#include <numeric>
#include <stdexcept>
#include <vector>

namespace agent {
// 公共逻辑时钟：倍率只改变墙钟间隔，不改变样本、种子或业务时间。
class SyncClock {
    uint64_t interval_ms_=0, logical_ms_=0, frame_=0;
    unsigned speed_quarters_=4;
public:
    explicit SyncClock(const std::vector<uint64_t> &intervals) {
        if(intervals.empty() || intervals.size()>16)throw std::runtime_error("同步组需要 1 至 16 个事件");
        for(auto interval:intervals) {
            if(interval<1 || interval>60000)throw std::runtime_error("事件周期必须为 1 至 60000ms");
            interval_ms_=std::gcd(interval_ms_,interval);
        }
    }
    void speed(double value) {
        if(value!=0.25 && value!=0.5 && value!=1 && value!=2 && value!=4)
            throw std::runtime_error("同步倍率仅支持 0.25、0.5、1、2、4");
        speed_quarters_=static_cast<unsigned>(value*4);
    }
    double speed() const {return speed_quarters_/4.0;}
    uint64_t interval_ms() const {return interval_ms_;}
    uint64_t logical_ms() const {return logical_ms_;}
    uint64_t frame() const {return frame_;}
    bool due(uint64_t interval) const {return logical_ms_%interval==0;}
    std::chrono::microseconds wall_interval() const {
        return std::chrono::microseconds(interval_ms_*4000/speed_quarters_);
    }
    void require_advance() const {
        if(logical_ms_>UINT64_MAX-interval_ms_ || frame_==UINT64_MAX)
            throw std::runtime_error("同步时间或样本序号超出运行预算");
    }
    void advance() {require_advance();logical_ms_+=interval_ms_;++frame_;}
};
}
