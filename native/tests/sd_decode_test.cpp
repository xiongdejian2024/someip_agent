#include "wire.hpp"

using namespace agent;
namespace {
void require(bool condition,const char *message) {
    if(!condition)throw std::runtime_error(message);
}
Bytes frame(const std::string &payload) {
    auto data=unhex("ffff8100000000080000000101010200"+payload);
    uint32_t length=data.size()-8;
    for(size_t i=0;i<4;++i)data[4+i]=uint8_t(length>>(24-8*i));
    return data;
}
Json decode(const std::string &payload) {
    auto data=frame(payload);return decode_packet(data.data(),data.size());
}
void reject(const std::string &payload) {
    auto result=decode(payload);
    require(result.contains("sd_error") && !result.contains("sd"),"畸形 SD 被误认为完整成功");
    require(result["is_sd"]==true && result["payload_hex"]==payload,"SD 失败丢失原始报文");
}
}
int main() {
    try {
        const std::string offer="01000000123400010100000300000002";
        auto good=decode("c000000000000010"+offer+"00000000");
        require(good["sd"]["decoder"]=="vsomeip-3.5.10" && good["sd"]["flags"]==192,
                "SD 解码来源或 flags 错误");
        const auto &entry=good["sd"]["entries"][0];
        require(entry["service_id"]==0x1234 && entry["ttl"]==3 && entry["minor_version"]==2,
                "Offer 标识、TTL 或版本解码错误");
        auto empty=decode("000000000000000000000000");
        require(empty["sd"]["entries"].empty() && empty["sd"]["options"].empty(),"合法空 SD 失败");
        auto group=decode("40000000000000100700000012340001010000000003000700000000");
        require(group["sd"]["entries"][0]["ttl"]==0 &&
                group["sd"]["entries"][0]["counter"]==3 &&
                group["sd"]["entries"][0]["eventgroup_id"]==7,"Nack/Counter 解码错误");
        auto option=decode("c000000000000010010000101234000101000003000000020000000c000904000a4d0001117735");
        require(option.contains("sd_error"),"截断 endpoint Option 被接受");
        auto endpoint=decode("c000000000000010010000101234000101000003000000020000000c000904000a4d000100117735");
        require(endpoint["sd"]["options"][0]["option_type"]==4 &&
                endpoint["sd"]["entries"][0]["option_indices"][0]==Json::array({0}),
                "Endpoint Option 或引用 run 解码错误");
        reject("c000000000000010"+offer); // 缺少 options-length。
        reject("c000000000000010"+offer+"0000000000"); // 尾随字节。
        reject("c00000000000000f"+offer+"00000000"); // Entry 长度不整齐。
        reject("c0000000000000100100001012340001010000030000000200000000"); // 引用越界。
        reject("c000000000000010"+offer+"0000000100"); // Option 不完整。
        std::cout<<Json{{"message","固定 vsomeip SD 模型与完整性边界测试通过"}}.dump()<<std::endl;
    } catch(const std::exception &error) {log_error("test.sd.decode",error);return 1;}
    return 0;
}
