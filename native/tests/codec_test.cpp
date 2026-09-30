#include "codec.hpp"

using namespace agent;
void require(bool condition,const char *message) {
    if(!condition)throw std::runtime_error(message);
}
template<class Function> void rejects(Function function) {
    try {function();}catch(const std::exception &){return;}
    throw std::runtime_error("非法输入未被拒绝");
}
int main() {
    try {
        for(auto type:{"uint8","uint16","uint32","uint64","int8","int16","int32","int64"}) {
            Json schema={{"type",type}};
            Json value=std::string(type)[0]=='i'?Json(-1):Json(42);
            auto bytes=Codec::encode(schema,value);
            require(Codec::decode(schema,bytes)==value,"整数往返失败");
        }
        require(hex(Codec::encode(Json{{"type","uint16"}},240))=="00f0","uint16 网络字节序不一致");
        require(hex(Codec::encode(Json{{"type","float32"}},12.5))=="41480000","float32 黄金字节不一致");
        require(hex(Codec::encode(Json{{"type","uint16"},{"byte_order","little"}},240))=="f000","小端序不一致");
        for(auto type:{"float32","float64","boolean","string","bytes"}) {
            Json value;
            if(std::string(type)=="boolean")value=false;
            else if(std::string(type)=="string")value="中文字符串";
            else if(std::string(type)=="bytes")value="0001ff";
            else value=12.5;
            Json schema={{"type",type}};
            require(Codec::decode(schema,Codec::encode(schema,value))==value,"基础类型往返失败");
        }
        Json array={{"type","array"},{"element",Json{{"type","int16"}}}};
        auto values=Json::array({-1,0,32767});
        require(Codec::decode(array,Codec::encode(array,values))==values,"动态数组往返失败");
        array["length"]=3;
        require(Codec::decode(array,Codec::encode(array,values))==values,"定长数组往返失败");
        rejects([&]{Codec::encode(array,Json::array({1}));});
        rejects([]{Codec::encode(Json{{"type","uint16"}},65536);});
        rejects([]{Codec::encode(Json{{"type","uint8"}},-1);});
        rejects([]{Codec::decode(Json{{"type","uint32"}},Bytes{0});});
        rejects([]{Codec::decode(Json{{"type","boolean"}},Bytes{2});});
        rejects([]{Codec::decode(Json{{"type","uint8"}},Bytes{1,2});});
        rejects([]{Codec::encode(Json{{"type","string"},{"length_bytes",0}},"x");});
        rejects([]{Codec::encode(Json{{"type","string"},{"length_bytes",9}},"x");});
        rejects([]{Codec::encode(Json{{"type","string"},{"length_bytes",1}},std::string(256,'x'));});
        rejects([]{number(Json(-1));});
        rejects([]{bounded_number(Json(65536),65535,"service_id");});
        std::cout<<Json{{"message","原生编解码基础类型、字节序、数组及异常边界测试通过"}}.dump()<<std::endl;
    }catch(const std::exception &error){log_error("test.codec",error);return 1;}
    return 0;
}
