#include "signal_source.hpp"

using namespace agent;
void require(bool condition,const char *message) {
    if(!condition)throw std::runtime_error(message);
}
template<class Function> void rejects(Function function) {
    try {function();}catch(const std::exception &){return;}
    throw std::runtime_error("非法激励未被拒绝");
}
int main() {
    try {
        Json u64={{"type","uint64"}},i64={{"type","int64"}},u8={{"type","uint8"}};
        SignalSource high(u64,Json{{"kind","constant"},{"initial",UINT64_MAX}});
        require(high.value(0,0)==Json(UINT64_MAX),"uint64 常量损失精度");
        require(hex(Codec::encode(u64,high.value(0,0)))=="ffffffffffffffff","uint64 黄金字节错误");
        SignalSource low(i64,Json{{"kind","constant"},{"initial",INT64_MIN}});
        require(low.value(1,3)==Json(INT64_MIN),"int64 常量损失精度");
        require(hex(Codec::encode(i64,low.value(0,0)))=="8000000000000000","int64 黄金字节错误");
        Json config={{"kind","sequence"},{"initial",0},{"sequence",Json::array({UINT64_MAX,0ULL,UINT64_MAX-1})}};
        SignalSource sequence(u64,config);
        config["sequence"][0]=3;
        require(sequence.value(0,0)==Json(UINT64_MAX) && sequence.value(0,2)==Json(UINT64_MAX-1)
            && sequence.value(0,3)==Json(UINT64_MAX),"序列快照或循环不正确");
        SignalSource empty(u8,Json{{"kind","sequence"},{"initial",42}});
        require(empty.value(2,100)==42,"空序列未保持旧初值语义");
        Json random_config={{"kind","random"},{"minimum",0ULL},{"maximum",UINT64_MAX},{"seed",UINT64_MAX}};
        SignalSource first(u64,random_config),second(u64,random_config),independent(u64,random_config);
        SignalSource different(u64,Json{{"kind","random"},{"minimum",0ULL},{"maximum",UINT64_MAX},{"seed",7}});
        bool differs=false;
        for(uint64_t index=0;index<1000;++index) {
            auto a=first.value(0,index),b=second.value(0,index);
            for(int i=0;i<3;++i)independent.value(0,index);
            require(a.is_number_unsigned() && a==b,"独立随机源互相干扰或丢失整数类型");
            require(Codec::decode(u64,Codec::encode(u64,a))==a,"随机 uint64 编码不一致");
            differs=differs || different.value(0,index)!=a;
        }
        require(differs,"不同种子产生相同随机流");
        SignalSource exact(u64,Json{{"kind","random"},{"minimum",UINT64_MAX},{"maximum",UINT64_MAX}});
        require(exact.value(0,0)==Json(UINT64_MAX),"随机 uint64 最大边界丢失精度");
        SignalSource signed_range(i64,Json{{"kind","random"},{"minimum",INT64_MIN},{"maximum",INT64_MAX}});
        for(int i=0;i<100;++i)Codec::encode(i64,signed_range.value(0,i));
        SignalSource ramp(u8,Json{{"kind","ramp"},{"minimum",0},{"maximum",10},{"period_seconds",4}});
        require(ramp.value(1,0)==2 && ramp.value(3,1)==7 && ramp.value(4,2)==0,"整数斜坡语义错误");
        SignalSource sine(Json{{"type","float32"}},Json{{"kind","sine"},{"minimum",0},{"maximum",10},{"period_seconds",4}});
        require(std::abs(sine.value(1,0).get<double>()-10)<1e-9,"正弦峰值错误");
        SignalSource boolean(Json{{"type","boolean"}},Json{{"kind","constant"},{"initial",true}});
        require(boolean.value(0,0)==true,"布尔初值错误");
        SignalSource rounded(Json{{"type","float32"}},Json{{"kind","constant"},{"initial",0.1}});
        require(rounded.value(0,0)==Json(0.10000000149011612),"float32 激励观测未反映实际编码量化");
        require(hex(Codec::encode(Json{{"type","float32"}},rounded.value(0,0)))=="3dcccccd",
            "float32 量化后黄金字节错误");
        rejects([&]{SignalSource source(u64,Json{{"initial",9007199254740992.0}});});
        rejects([&]{SignalSource source(u64,Json{{"initial",1.5}});});
        rejects([&]{SignalSource source(u64,Json{{"initial",-1}});});
        rejects([&]{SignalSource source(i64,Json{{"initial",UINT64_MAX}});});
        rejects([&]{SignalSource source(u8,Json{{"kind","sequence"},{"sequence",Json::array({1,999})}});});
        rejects([&]{SignalSource source(u8,Json{{"seed",true}});});
        rejects([&]{SignalSource source(u8,Json{{"seed",-1}});});
        rejects([&]{SignalSource source(u8,Json{{"seed",1.5}});});
        rejects([&]{SignalSource source(u8,Json{{"kind","random"},{"minimum",100},{"maximum",1}});});
        rejects([&]{SignalSource source(u64,Json{{"kind","sine"},{"maximum",UINT64_MAX}});});
        rejects([&]{SignalSource source(u8,Json{{"period_seconds",0}});});
        rejects([&]{SignalSource source(u8,Json{{"period_seconds",std::numeric_limits<double>::infinity()}});});
        rejects([&]{SignalSource source(u8,Json{{"kind","arbitrary-script"}});});
        rejects([&]{SignalSource source(Json{{"type","struct"}},Json::object());});
        rejects([&]{sequence.value(-1,0);});
        std::cout<<"原生激励整数边界、序列快照与每任务随机种子验证通过"<<std::endl;
        return 0;
    } catch(const std::exception &error) {log_error("signal_source.test",error);return 1;}
}
