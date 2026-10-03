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
        Json boolean_random_config={{"kind","random"},{"minimum",0},{"maximum",1},{"seed",42}};
        SignalSource boolean_random(Json{{"type","boolean"}},boolean_random_config);
        SignalSource boolean_repeat(Json{{"type","boolean"}},boolean_random_config);
        bool seen_false=false,seen_true=false;
        for(int i=0;i<64;++i) {
            auto value=boolean_random.value(0,i);
            require(value.is_boolean() && value==boolean_repeat.value(0,i),"布尔随机种子未复现");
            seen_true=seen_true || value.get<bool>();seen_false=seen_false || !value.get<bool>();
        }
        require(seen_true && seen_false,"布尔随机未从两个离散值中采样");
        SignalSource false_random(Json{{"type","boolean"}},Json{{"kind","random"},{"minimum",0},{"maximum",0}});
        require(false_random.value(0,0)==false,"布尔随机定值范围错误");
        rejects([&]{SignalSource source(Json{{"type","boolean"}},Json{{"kind","random"},{"minimum",0},{"maximum",2}});});
        SignalSource rounded(Json{{"type","float32"}},Json{{"kind","constant"},{"initial",0.1}});
        require(rounded.value(0,0)==Json(0.10000000149011612),"float32 激励观测未反映实际编码量化");
        require(hex(Codec::encode(Json{{"type","float32"}},rounded.value(0,0)))=="3dcccccd",
            "float32 量化后黄金字节错误");
        SignalSource step(u64,Json{{"kind","step"},{"initial",UINT64_MAX},{"step_at_ms",29},{"step_value",UINT64_MAX-1}});
        require(step.value_ms(28,0)==UINT64_MAX && step.value_ms(29,1)==UINT64_MAX-1
            && step.value_ms(100000,2)==UINT64_MAX-1,"阶跃边界/保持或整数精度错误");
        SignalSource long_step(i64,Json{{"kind","step"},{"initial",INT64_MIN},{"step_at_ms",UINT64_MAX},{"step_value",INT64_MAX}});
        require(long_step.value_ms(UINT64_MAX-1,0)==INT64_MIN && long_step.value_ms(UINT64_MAX,1)==INT64_MAX,
            "64 位阶跃时刻经过浮点舍入");
        SignalSource immediate(Json{{"type","float32"}},Json{{"kind","step"},{"step_at_ms",0},{"step_value",0.1}});
        require(immediate.value_ms(0,0)==0.10000000149011612,"首样本阶跃未量化");
        SignalSource text(Json{{"type","string"}},Json{{"kind","step"},{"initial","before"},{"step_at_ms",1},{"step_value","after"}});
        require(text.value_ms(0,0)=="before" && text.value_ms(1,1)=="after","文本阶跃错误");
        SignalSource flag(Json{{"type","boolean"}},Json{{"kind","step"},{"initial",false},{"step_at_ms",1},{"step_value",true}});
        require(flag.value_ms(0,0)==false && flag.value_ms(1,1)==true,"布尔阶跃错误");
        rejects([&]{SignalSource source(u8,Json{{"kind","step"},{"step_at_ms",1}});});
        rejects([&]{SignalSource source(u8,Json{{"kind","step"},{"step_value",1}});});
        rejects([&]{SignalSource source(u8,Json{{"kind","step"},{"step_at_ms",true},{"step_value",1}});});
        rejects([&]{SignalSource source(u8,Json{{"kind","step"},{"step_at_ms",-1},{"step_value",1}});});
        rejects([&]{SignalSource source(u8,Json{{"kind","step"},{"step_at_ms",29.0},{"step_value",1}});});
        rejects([&]{SignalSource source(u8,Json{{"kind","step"},{"step_at_ms",1},{"step_value",256}});});
        rejects([&]{SignalSource source(u8,Json{{"kind","constant"},{"step_at_ms",1}});});
        rejects([&]{step.value(0.029,0);});
        auto timeline=[](Json first,Json second) {return Json{{"kind","csv"},{"timeline",Json::array({
            Json{{"at_ms",0},{"value",first}},Json{{"at_ms",29},{"value",second}}})}};};
        Json csv_config=timeline(UINT64_MAX,UINT64_MAX-1);
        csv_config["timeline"].push_back(Json{{"at_ms",UINT64_MAX},{"value",0ULL}});
        SignalSource csv(u64,csv_config);
        require(csv.needs_millisecond_clock() && csv.value_ms(0,0)==UINT64_MAX && csv.value_ms(28,2)==UINT64_MAX
            && csv.value_ms(29,3)==UINT64_MAX-1 && csv.value_ms(UINT64_MAX-1,4)==UINT64_MAX-1
            && csv.value_ms(UINT64_MAX,5)==0,"CSV 零阶保持或整型时间精度错误");
        csv_config["timeline"][0]["value"]=1;
        require(csv.value_ms(0,0)==UINT64_MAX,"CSV 源未冻结调用方数据");
        SignalSource csv_signed(i64,timeline(INT64_MIN,INT64_MAX));
        require(csv_signed.value_ms(28,1)==INT64_MIN && csv_signed.value_ms(1000,2)==INT64_MAX,
            "CSV 有符号边界或末值保持错误");
        SignalSource csv_float(Json{{"type","float32"}},timeline(0.1,42.5));
        require(csv_float.value_ms(0,0)==0.10000000149011612 && csv_float.value_ms(29,0)==42.5,
            "CSV float32 未按实际 Codec 量化");
        SignalSource csv_flag(Json{{"type","boolean"}},timeline(false,true));
        require(csv_flag.value_ms(0,0)==false && csv_flag.value_ms(29,0)==true,"CSV 布尔类型丢失");
        SignalSource csv_text(Json{{"type","string"}},timeline("before","=SUM(1,2)"));
        require(csv_text.value_ms(29,1)=="=SUM(1,2)","CSV 文本未保持惰性字面量");
        rejects([&]{csv.value(0,0);});
        for(const Json &at:Json::array({true,-1,29.0,1})) {
            auto bad=timeline(1,2);bad["timeline"][0]["at_ms"]=at;
            rejects([&]{SignalSource source(u8,bad);});
        }
        for(const Json &at:Json::array({0,-1,true,29.0})) {
            auto bad=timeline(1,2);bad["timeline"][1]["at_ms"]=at;
            rejects([&]{SignalSource source(u8,bad);});
        }
        auto reversed=timeline(1,2);reversed["timeline"].push_back(Json{{"at_ms",28},{"value",3}});
        rejects([&]{SignalSource source(u8,reversed);});
        auto extra=timeline(1,2);extra["timeline"][1]["script"]="ignored";
        rejects([&]{SignalSource source(u8,extra);});
        auto missing=timeline(1,2);missing["timeline"][1].erase("value");
        rejects([&]{SignalSource source(u8,missing);});
        auto large=timeline(1,2);large["timeline"]=Json::array();
        for(int i=0;i<8193;++i)large["timeline"].push_back(Json{{"at_ms",i},{"value",1}});
        rejects([&]{SignalSource source(u8,large);});
        rejects([&]{SignalSource source(u8,Json{{"kind","csv"},{"timeline",Json::array()}});});
        rejects([&]{SignalSource source(u8,timeline(1,256));});
        rejects([&]{SignalSource source(u8,timeline(1,2.0));});
        rejects([&]{SignalSource source(u8,timeline(1,true));});
        rejects([&]{SignalSource source(Json{{"type","boolean"}},timeline(false,1));});
        rejects([&]{SignalSource source(Json{{"type","string"}},timeline("before",1));});
        rejects([&]{SignalSource source(u8,Json{{"kind","constant"},{"timeline",Json::array()}});});
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
        auto graph=[](Json first,Json second,uint64_t duration=29) {return Json{{"kind","state_machine"},
            {"initial_state","idle"},{"states",Json::array({
                Json{{"name","idle"},{"value",first},{"duration_ms",duration},{"next","active"}},
                Json{{"name","active"},{"value",second}}})}};};
        auto state_config=graph(UINT64_MAX,UINT64_MAX-1);
        SignalSource machine(u64,state_config);
        require(machine.active_state().empty(),"启动预检不能冒充实际采样");
        require(machine.value_ms(28,0)==UINT64_MAX && machine.active_state()=="idle"
            && machine.value_ms(29,1)==UINT64_MAX-1 && machine.active_state()=="active"
            && machine.value_ms(UINT64_MAX,2)==UINT64_MAX-1,"状态机边界、终态保持或整数精度错误");
        state_config["states"][0]["value"]=0;
        require(machine.value_ms(0,0)==UINT64_MAX,"状态图未冻结");
        rejects([&]{machine.value(0,0);});
        SignalSource signed_machine(i64,graph(INT64_MIN,INT64_MAX,UINT64_MAX));
        require(signed_machine.value_ms(UINT64_MAX-1,0)==INT64_MIN
            && signed_machine.value_ms(UINT64_MAX,1)==INT64_MAX,"状态图 uint64 时刻发生舍入");
        SignalSource float_machine(Json{{"type","float32"}},graph(0.1,42.5));
        require(float_machine.value_ms(0,0)==0.10000000149011612
            && float_machine.value_ms(29,0)==42.5,"状态值未按冻结 Codec 量化");
        SignalSource flag_machine(Json{{"type","boolean"}},graph(false,true));
        require(flag_machine.value_ms(0,0)==false && flag_machine.value_ms(29,0)==true,"布尔状态值错误");
        SignalSource text_machine(Json{{"type","string"}},graph("before","=SUM(1,2)"));
        require(text_machine.value_ms(29,0)=="=SUM(1,2)","文本状态不应执行表达式");
        SignalSource bytes_machine(Json{{"type","bytes"}},graph("01","ab"));
        require(bytes_machine.value_ms(29,0)=="ab","字节状态值错误");
        auto cyclic=graph(1,2,3);
        cyclic["states"][1]["duration_ms"]=5;cyclic["states"][1]["next"]="idle";
        SignalSource loop(u8,cyclic);
        for(uint64_t at:std::vector<uint64_t>{0,2,3,7,8,11,UINT64_MAX})
            require(loop.value_ms(at,0)==(at%8<3?1:2),"状态循环取模错误");
        cyclic["states"].push_back(Json{{"name","prefix"},{"value",7},{"duration_ms",2},{"next","idle"}});
        cyclic["initial_state"]="prefix";
        SignalSource prefix(u8,cyclic);
        require(prefix.value_ms(1,0)==7 && prefix.value_ms(2,0)==1 && prefix.value_ms(5,0)==2
            && prefix.value_ms(10,0)==1 && prefix.value_ms(UINT64_MAX,0)==((UINT64_MAX-2)%8<3?1:2),
            "前缀进入循环或超长运行错误");
        SignalSource self(u8,Json{{"kind","state_machine"},{"initial_state","hold"},{"states",Json::array({
            Json{{"name","hold"},{"value",5},{"duration_ms",1},{"next","hold"}}})}});
        require(self.value_ms(UINT64_MAX,0)==5,"自循环状态错误");
        for(const Json &duration:Json::array({0,-1,true,29.0})) {
            auto bad=graph(1,2);bad["states"][0]["duration_ms"]=duration;
            rejects([&]{SignalSource source(u8,bad);});
        }
        for(const auto &field:{"duration_ms","next","value","name"}) {
            auto bad=graph(1,2);bad["states"][0].erase(field);
            rejects([&]{SignalSource source(u8,bad);});
        }
        auto bad=graph(1,256);rejects([&]{SignalSource source(u8,bad);});
        bad=graph(1,2.0);rejects([&]{SignalSource source(u8,bad);});
        bad=graph(false,1);rejects([&]{SignalSource source(Json{{"type","boolean"}},bad);});
        bad=graph("valid",1);rejects([&]{SignalSource source(Json{{"type","string"}},bad);});
        for(const auto &change:Json::array({
            Json{{"name","idle"},{"value",3}},Json{{"name","unused"},{"value",3}},
            Json{{"name","active"},{"value",3},{"duration_ms",2}},
            Json{{"name","active"},{"value",3},{"script","exec"}}})) {
            bad=graph(1,2);bad["states"].push_back(change);
            rejects([&]{SignalSource source(u8,bad);});
        }
        bad=graph(1,2,UINT64_MAX);bad["states"][1]["duration_ms"]=1;bad["states"][1]["next"]="idle";
        rejects([&]{SignalSource source(u8,bad);});
        bad=graph(1,2);bad["initial_state"]="missing";rejects([&]{SignalSource source(u8,bad);});
        bad=graph(1,2);bad["states"][0]["next"]="missing";rejects([&]{SignalSource source(u8,bad);});
        SignalSource defaults(u8,Json{{"initial",3},{"states",Json::array()},{"initial_state",nullptr}});
        require(defaults.value(0,0)==3,"默认空图破坏旧契约");
        rejects([&]{SignalSource source(u8,Json{{"states",Json::array({Json{{"name","x"},{"value",1}}})}});});
        std::cout<<"原生激励整数边界、序列快照与每任务随机种子验证通过"<<std::endl;
        return 0;
    } catch(const std::exception &error) {log_error("signal_source.test",error);return 1;}
}
