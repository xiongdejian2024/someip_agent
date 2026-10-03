#include "event_stimulus.hpp"
using namespace agent;
void require(bool condition,const char *message) {if(!condition)throw std::runtime_error(message);}
template<class Function> void rejects(Function function) {
    try {function();}catch(const std::exception &){return;}
    throw std::runtime_error("非法事件激励未拒绝");
}
int main() {
    try {
        Json schema={{"type","struct"},{"fields",Json::array({
            Json{{"name","counter"},{"type","uint64"},{"byte_order","little"}},
            Json{{"name","samples"},{"type","array"},{"length",2},{"element",Json{{"type","uint16"}}}},
            Json{{"name","nested"},{"type","struct"},{"fields",Json::array({Json{{"name","a/b~"},{"type","float32"}}})}}
        })}};
        Json arguments={{"counter",0},{"samples",Json::array({0x1234,0xabcd})},{"nested",Json{{"a/b~",0}}}};
        Json sources=Json::array({
            Json{{"path","/counter"},{"generator",Json{{"kind","sequence"},{"sequence",Json::array({UINT64_MAX,UINT64_MAX-1})}}}},
            Json{{"path","/samples/1"},{"generator",Json{{"kind","sequence"},{"sequence",Json::array({2,3})}}}},
            Json{{"path","/nested/a~1b~0"},{"generator",Json{{"kind","constant"},{"initial",0.1}}}}
        });
        EventStimulus event(schema,arguments,sources);
        require(event.source_count()==3,"激励绑定数错误");
        require(hex(event.sample(0,0))=="ffffffffffffffff123400023dcccccd","完整多信号黄金字节错误");
        require(hex(event.sample(1,1))=="feffffffffffffff123400033dcccccd","序列索引或小端错误");
        sources[0]["generator"]["sequence"][0]=7;
        require(hex(event.sample(2,2))=="ffffffffffffffff123400023dcccccd","调用方修改影响冻结源");
        bool truncated;
        auto values=event_signal_values(Codec::decode(schema,event.sample(3,3)),truncated);
        require(!truncated && values["/counter"]==UINT64_MAX-1 && values["/samples/1"]==3
            && values["/nested/a~1b~0"]==0.10000000149011612,"实际解码信号或路径转义错误");
        auto large=Json::array();for(int i=0;i<200;++i)large.push_back(i);
        require(event_signal_values(large,truncated).size()==128 && truncated,"波形叶节点预算未生效");
        for(const auto &path:{"/missing","/samples","/samples/-","/samples/02","/samples/2","/counter/child","/nested/a~2b"})
            rejects([&]{EventStimulus bad(schema,arguments,Json::array({Json{{"path",path},{"generator",Json::object()}}}));});
        auto duplicate=sources;duplicate.push_back(sources[0]);
        rejects([&]{EventStimulus bad(schema,arguments,duplicate);});
        auto invalid=sources;invalid[1]["generator"]["sequence"]=Json::array({1,65536});
        rejects([&]{EventStimulus bad(schema,arguments,invalid);});
        invalid=sources;invalid[0]["generator"]["data_type"]="uint8";
        rejects([&]{EventStimulus bad(schema,arguments,invalid);});
        EventStimulus scalar(Json{{"type","uint64"}},0,Json::array({Json{{"path",""},
            {"generator",Json{{"initial",UINT64_MAX}}}}}));
        require(hex(scalar.sample(0,0))=="ffffffffffffffff","根标量激励失败");
        EventStimulus step(Json{{"type","uint64"}},0,Json::array({Json{{"path",""},
            {"generator",Json{{"kind","step"},{"initial",UINT64_MAX},{"step_at_ms",29},{"step_value",UINT64_MAX-1}}}}}));
        require(hex(step.sample_ms(28,0))=="ffffffffffffffff" && hex(step.sample_ms(29,1))=="fffffffffffffffe",
            "完整事件阶跃未使用精确毫秒时间");
        Json random_config={{"kind","random"},{"minimum",0ULL},{"maximum",UINT64_MAX},{"seed",19}};
        SignalSource reference(Json{{"type","uint64"}},random_config);
        EventStimulus random_event(Json{{"type","uint64"}},0,Json::array({Json{{"path",""},{"generator",random_config}}}));
        for(uint64_t index=0;index<20;++index)require(
            Codec::decode(Json{{"type","uint64"}},random_event.sample(index,index))==reference.value(index,index),
            "事件预检消耗了正式随机流");
        Json vsa={{"type","array"},{"max_length",8},{"vsa",Json{{"profile","VSA_LINEAR"},{"size_name","size"},
            {"payload_name","payload"},{"size_type","uint8"}}},{"element",Json{{"type","uint16"}}}};
        Json data={{"size",2},{"payload",Json::array({1,2})}};
        EventStimulus linear(vsa,data,Json::array({Json{{"path","/payload/1"},{"generator",Json{{"initial",9}}}}}));
        require(Codec::decode(vsa,linear.sample(0,0))["payload"][1]==9,"VSA 有效元素绑定失败");
        rejects([&]{EventStimulus bad(vsa,data,Json::array({Json{{"path","/size"},{"generator",Json::object()}}}));});
        std::cout<<"完整事件结构/数组路径绑定、原布局、精确整数与观测预算验证通过"<<std::endl;
        return 0;
    }catch(const std::exception &error){log_error("event_stimulus.test",error);return 1;}
}
