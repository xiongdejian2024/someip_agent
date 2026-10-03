#include "sync_clock.hpp"
#include <cassert>
#include <iostream>
int main() {
    agent::SyncClock clock({20,30,60});
    assert(clock.interval_ms()==10 && clock.frame()==0 && clock.logical_ms()==0);
    assert(clock.due(20) && clock.due(30));
    clock.advance();assert(!clock.due(20) && !clock.due(30));
    clock.advance();assert(clock.due(20) && !clock.due(30));
    clock.advance();assert(!clock.due(20) && clock.due(30));
    for(auto speed:{0.25,0.5,1.0,2.0,4.0}) {
        clock.speed(speed);assert(clock.wall_interval().count()==static_cast<int64_t>(10000/speed));
        assert(clock.logical_ms()==30 && clock.frame()==3);
    }
    bool rejected=false;try {clock.speed(3);}catch(const std::runtime_error &) {rejected=true;}
    assert(rejected && clock.speed()==4);
    for(auto intervals:{std::vector<uint64_t>{},std::vector<uint64_t>{0},std::vector<uint64_t>{60001}}) {
        rejected=false;try {agent::SyncClock invalid(intervals);}catch(const std::runtime_error &) {rejected=true;}
        assert(rejected);
    }
    agent::SyncClock millisecond({29,1});millisecond.speed(4);
    assert(millisecond.wall_interval().count()==250);
    std::cout<<"公共整型时钟、异周期采样、倍率与非法配置门禁通过\n";
}
