#include "codec.hpp"
#include "payload_decode.hpp"

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
        Json observed={{"type","struct"},{"fields",Json::array({Json{{"name","sample"},{"type","uint16"}}})}};
        auto batch=decode_payload_batch(Json{{"schema",observed},{"payloads",Json::array({"0032","ff","003200"})}});
        require(batch["decoder"]=="someip-agent-native-codec" && batch["records"][0]["values"]["sample"]==50,
                "原生观测批量解码来源或黄金值错误");
        require(batch["records"][1].contains("error") && batch["records"][2].contains("error"),
                "批量解码未保留截断或尾随错误");
        rejects([&]{decode_payload_batch(Json{{"schema",observed},{"payloads",Json::array()}});});
        rejects([&]{decode_payload_batch(Json{{"schema",observed},{"payloads",std::vector<std::string>(33,"0032")}});});
        rejects([&]{decode_payload_batch(Json{{"schema",observed},{"payloads",Json::array({std::string(131074,'0')})}});});
        rejects([&]{decode_payload_batch(Json{{"schema",observed},{"payloads",std::vector<std::string>(5,std::string(131072,'0'))}});});
        Json nonfinite={{"type","struct"},{"fields",Json::array({Json{{"name","sample"},{"type","float32"}}})}};
        require(decode_payload_batch(Json{{"schema",nonfinite},{"payloads",Json::array({"7f800000"})}})["records"][0].contains("error"),
                "非有限原生值被静默序列化为null");
        Bytes all_bytes;
        std::ostringstream reference;
        for(unsigned int value=0;value<256;++value) {
            all_bytes.push_back(static_cast<uint8_t>(value));
            reference<<std::hex<<std::setw(2)<<std::setfill('0')<<value;
        }
        require(hex({}).empty(),"空 payload 十六进制转换错误");
        require(hex(all_bytes)==reference.str(),"所有字节的小写十六进制转换不一致");
        require(unhex(hex(all_bytes))==all_bytes,"所有字节的十六进制往返不一致");
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
        Json prefixed={{"type","struct"},{"length_bytes",2},{"fields",Json::array({
            Json{{"name","tag"},{"type","uint8"}},
            Json{{"name","values"},{"type","array"},{"length",2},{"length_bytes",2},
                {"element",Json{{"type","uint16"}}}}
        })}};
        Json structured={{"tag",7},{"values",Json::array({0x1234,0xabcd})}};
        require(hex(Codec::encode(prefixed,structured))=="00070700041234abcd","结构/定长数组长度前缀黄金字节错误");
        require(Codec::decode(prefixed,unhex("00070700041234abcd"))==structured,"结构/数组长度前缀解码失败");
        rejects([&]{Codec::decode(prefixed,unhex("00060700041234abcd"));});
        rejects([&]{Codec::decode(prefixed,unhex("00070700031234abcd"));});
        rejects([&]{Codec::decode(prefixed,unhex("ffff0700041234abcd"));});
        prefixed["length_bytes"]=3;
        rejects([&]{Codec::encode(prefixed,structured);});
        rejects([&]{Codec::decode(prefixed,unhex("0000070700041234abcd"));});
        Json bounded={{"type","array"},{"max_length",2},{"length_bytes",1},{"element",Json{{"type","uint8"}}}};
        require(hex(Codec::encode(bounded,Json::array({1,2})))=="020102","有界数组黄金字节错误");
        rejects([&]{Codec::encode(bounded,Json::array({1,2,3}));});
        rejects([&]{Codec::decode(bounded,unhex("03010203"));});
        // 绝对偏移包含 SOME/IP 头和外层长度前缀，不能在临时 body 的起点重新对齐。
        Json aligned={{"type","struct"},{"length_bytes",2},{"alignment_bytes",8},
            {"fields",Json::array({
                Json{{"name","tag"},{"type","uint8"},{"alignment_bytes",8}},
                Json{{"name","items"},{"type","array"},{"length_bytes",1},{"max_length",3},
                    {"alignment_bytes",8},{"element",Json{{"type","uint16"}}}},
                Json{{"name","tail"},{"type","uint16"},{"alignment_bytes",8}}
            })}};
        Json aligned_value={{"tag",7},{"items",Json::array({0x1234})},{"tail",0xabcd}};
        require(hex(Codec::encode(aligned,aligned_value))=="0008070212340000abcd","变长数组全消息对齐黄金字节错误");
        require(Codec::decode(aligned,unhex("0008070212340000abcd"))==aligned_value,"变长对齐解码错误");
        aligned_value["items"]=Json::array();
        require(hex(Codec::encode(aligned,aligned_value))=="0008070000000000abcd","空变长数组未按绝对位置对齐");
        require(Codec::decode(aligned,Codec::encode(aligned,aligned_value))==aligned_value,"空变长数组解码失败");
        rejects([&]{Codec::decode(aligned,unhex("000607021234abcd"));});
        aligned["fields"][1]["alignment_bytes"]=0;
        rejects([&]{Codec::encode(aligned,aligned_value);});
        aligned["fields"][1]["alignment_bytes"]=8;
        aligned["fields"].erase(aligned["fields"].end()-1);
        aligned_value.erase("tail");aligned_value["items"]=Json::array({0x1234});
        require(hex(Codec::encode(aligned,aligned_value))=="000407021234","消息末尾不得自动加 padding");
        require(Codec::decode(aligned,Codec::encode(aligned,aligned_value))==aligned_value,"末尾变长数组解码失败");
        Json fixed={{"type","struct"},{"alignment_bytes",8},{"fields",Json::array({
            Json{{"name","a"},{"type","uint8"},{"alignment_bytes",8}},
            Json{{"name","b"},{"type","uint32"},{"alignment_bytes",8}}
        })}};
        require(hex(Codec::encode(fixed,Json{{"a",1},{"b",0x12345678}}))=="0112345678","固定成员被错误对齐");
        Json strings={{"type","array"},{"length_bytes",2},{"alignment_bytes",8},
            {"element",Json{{"type","string"},{"length_bytes",1},{"alignment_bytes",8}}}};
        auto text_values=Json::array({"a","bc"});
        require(hex(Codec::encode(strings,text_values))=="0009016100000000026263","变长元素数组黄金字节错误");
        require(Codec::decode(strings,Codec::encode(strings,text_values))==text_values,"变长元素数组对齐解码失败");
        // 字典里的 indicator 是元素数量；线上只写一个数组字节长度，不序列化 count。
        for(auto width:{1,2,4})for(auto little:{false,true}) {
            Json vsa={{"type","array"},{"max_length",3},{"length_bytes",width},
                {"alignment_bytes",8},{"byte_order",little?"little":"big"},
                {"element",Json{{"type","uint16"},{"byte_order",little?"little":"big"}}},
                {"vsa",Json{{"profile","VSA_LINEAR"},{"size_name","validElements"},
                    {"payload_name","words"},{"size_type","uint"+std::to_string(width*8)}}}};
            Json value={{"validElements",2},{"words",Json::array({0x1234,0xabcd})}};
            auto expected=unhex(little?"3412cdab":"1234abcd");
            Bytes prefix(width,0);prefix[little?0:width-1]=4;
            expected.insert(expected.begin(),prefix.begin(),prefix.end());
            require(Codec::encode(vsa,value)==expected,"VSA 字节长度与元素数量混淆或双前缀");
            require(Codec::decode(vsa,expected)==value,"VSA 未保留源字典字段");
            Json envelope={{"type","struct"},{"fields",Json::array({
                Json{{"name","tag"},{"type","uint8"}},Json(vsa),
                Json{{"name","tail"},{"type","uint8"}}
            })}};
            envelope["fields"][1]["name"]="data";
            Json nested={{"tag",7},{"data",value},{"tail",9}};
            auto golden=expected;golden.insert(golden.begin(),7);
            golden.insert(golden.end(),(-(16+golden.size()))%8,0);golden.push_back(9);
            require(Codec::encode(envelope,nested)==golden,"VSA 非末尾绝对对齐错误");
            require(Codec::decode(envelope,golden)==nested,"VSA 嵌套字典解码错误");
            Json empty={{"validElements",0},{"words",Json::array()}};
            require(Codec::decode(vsa,Codec::encode(vsa,empty))==empty,"空 VSA 往返失败");
            nested["data"]=empty;
            require(Codec::decode(envelope,Codec::encode(envelope,nested))==nested,"空 VSA 非末尾对齐失败");
            for(auto invalid:{Json(-1),Json(1),Json(3),Json(256),Json(true),Json(2.0),Json("2")}) {
                auto bad=value;bad["validElements"]=invalid;
                rejects([&]{Codec::encode(vsa,bad);});
            }
            auto missing=value;missing.erase("validElements");
            rejects([&]{Codec::encode(vsa,missing);});
            rejects([&]{Codec::encode(vsa,Json::array({0x1234,0xabcd}));});
            auto too_many=value;too_many["validElements"]=4;too_many["words"]=Json::array({1,2,3,4});
            rejects([&]{Codec::encode(vsa,too_many);});
            auto truncated=expected;truncated.pop_back();
            rejects([&]{Codec::decode(vsa,truncated);});
            auto wrong_length=expected;wrong_length[little?0:width-1]=3;
            rejects([&]{Codec::decode(vsa,wrong_length);});
            auto fixed_vsa=vsa;fixed_vsa["length"]=2;
            rejects([&]{Codec::encode(fixed_vsa,value);});
            rejects([&]{Codec::decode(fixed_vsa,expected);});
            auto unknown=vsa;unknown["vsa"]["profile"]="VSA_SQUARE";
            rejects([&]{Codec::encode(unknown,value);});
            rejects([&]{Codec::decode(unknown,expected);});
        }
        // 声明百万元素不分配百万个值；实际 70000 个 uint16 跨过旧上界。
        for(auto little:{false,true}) {
            Json large={{"type","array"},{"max_length",1048576},{"length_bytes",4},
                {"byte_order",little?"little":"big"},
                {"element",Json{{"type","uint16"},{"byte_order",little?"little":"big"}}},
                {"vsa",Json{{"profile","VSA_LINEAR"},{"size_name","validElements"},
                    {"payload_name","words"},{"size_type","uint32"}}}};
            Json words=Json::array();Bytes expected;
            const uint32_t wire_size=140000;
            for(size_t i=0;i<4;++i)expected.push_back((wire_size>>((little?i:3-i)*8))&0xff);
            for(size_t i=0;i<70000;++i) {
                uint16_t word=i%2?0xabcd:0x1234;words.push_back(word);
                expected.push_back(little?word&0xff:word>>8);
                expected.push_back(little?word>>8:word&0xff);
            }
            Json value={{"validElements",70000},{"words",std::move(words)}};
            require(Codec::encode(large,value)==expected,"大 VSA 字节长度/元素数量黄金编码错误");
            require(Codec::decode(large,expected)==value,"大 VSA 源字典解码错误");
            auto narrow=large;narrow["length_bytes"]=2;
            rejects([&]{Codec::encode(narrow,value);});
            large["max_length"]=65536;
            rejects([&]{Codec::encode(large,value);});
            rejects([&]{Codec::decode(large,expected);});
        }
        Json actual_limit={{"type","array"},{"max_length",UINT32_MAX},
            {"element",Json{{"type","uint64"}}}};
        auto oversized=Json::array();for(size_t i=0;i<max_frame/8+1;++i)oversized.push_back(0);
        rejects([&]{Codec::encode(actual_limit,oversized);});
        rejects([&]{Codec::decode(Json{{"type","bytes"}},Bytes(max_frame+1));});
        actual_limit["length"]=UINT32_MAX;
        rejects([&]{Codec::encode(actual_limit,Json::array({0}));});
        rejects([&]{Codec::decode(actual_limit,Bytes{});});
        rejects([]{number(Json(-1));});
        rejects([]{bounded_number(Json(65536),65535,"service_id");});
        std::cout<<Json{{"message","原生编解码基础类型、字节序、数组及异常边界测试通过"}}.dump()<<std::endl;
    }catch(const std::exception &error){log_error("test.codec",error);return 1;}
    return 0;
}
