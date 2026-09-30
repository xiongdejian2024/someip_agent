#include "capture_processor.hpp"
#include <tins/tins.h>

using namespace agent;
using namespace Tins;
namespace {
const Bytes golden = unhex("456780030000000c001100170101020041280000");
void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}
void fragment_stage(const char *message) {
    std::cout << Json{{"operation","test.capture.fragment.invalid"},{"message",message}}.dump() << std::endl;
}
struct Fixture {
    std::vector<Json> messages;
    std::vector<std::string> errors;
    CaptureProcessor processor;
    int64_t now = 100000000000;
    Fixture() : processor([this](const Json &m) { messages.push_back(m); },
                          [this](const std::exception &e) { errors.push_back(e.what()); }) {}
    void feed(PDU &pdu) {
        auto bytes = pdu.serialize();
        processor.feed(bytes.data(), bytes.size(), now += 1000000);
    }
    void tcp(uint32_t seq, uint32_t ack, uint16_t flags, const Bytes &data = {},
             bool server = false, bool ipv6 = false, uint16_t port = 41000) {
        TCP tcp(server ? port : 30608, server ? 30608 : port);
        tcp.seq(seq);tcp.ack_seq(ack);tcp.flags(flags);
        if (!data.empty()) tcp /= RawPDU(data);
        EthernetII ethernet("00:00:00:00:00:01", "00:00:00:00:00:02");
        if (ipv6) ethernet /= IPv6(server ? "fd77::2" : "fd77::1", server ? "fd77::1" : "fd77::2") / tcp;
        else ethernet /= IP(server ? "10.77.0.2" : "10.77.0.1", server ? "10.77.0.1" : "10.77.0.2") / tcp;
        feed(ethernet);
    }
    void handshake(uint32_t seq = 100, bool ipv6 = false, uint16_t port = 41000) {
        tcp(seq, 0, TCP::SYN, {}, false, ipv6, port);
        tcp(700, seq + 1, TCP::SYN | TCP::ACK, {}, true, ipv6, port);
        tcp(seq + 1, 701, TCP::ACK, {}, false, ipv6, port);
    }
    void check(size_t count) {
        require(messages.size() == count, "捕获重组后的报文数量错误");
        for (const auto &m : messages) {
            require(m.at("payload_hex") == "41280000", "捕获 payload 黄金字节错误");
            require(m.at("service_id") == 0x4567 && m.at("method_id") == 0x8003, "vsomeip 头解码错误");
        }
    }
};
Bytes part(size_t begin, size_t end) { return Bytes(golden.begin() + begin, golden.begin() + end); }
Bytes transport_payload(bool tcp=false) {
    IP packet("10.77.0.1","10.77.0.2");
    if(tcp) {
        TCP transport(30608,41000);transport.seq(101);transport.ack_seq(701);
        transport.flags(TCP::ACK|TCP::PSH);packet/=transport/RawPDU(golden);
    } else packet/=UDP(30608,41000)/RawPDU(golden);
    auto bytes=packet.serialize();
    return Bytes(bytes.begin()+((bytes[0]&0x0f)*4),bytes.end());
}
EthernetII fragment(const Bytes &payload,uint32_t offset,bool more,uint16_t id=99,
                    uint8_t protocol=17,bool reverse=false,uint16_t vlan=0) {
    IP ip(reverse ? "10.77.0.2":"10.77.0.1",reverse ? "10.77.0.1":"10.77.0.2");
    ip.id(id);ip.protocol(protocol);ip.fragment_offset(offset/8);
    ip.flags(more ? IP::MORE_FRAGMENTS:IP::Flags(0));ip/=RawPDU(payload);
    EthernetII ethernet("00:00:00:00:00:01","00:00:00:00:00:02");
    if(vlan)ethernet/=Dot1Q(vlan)/ip;else ethernet/=ip;
    // 直接调用重组器的夹具也使用实际线上头长度，而非未序列化对象的默认 IHL。
    auto raw=ethernet.serialize();
    return EthernetII(raw.data(),raw.size());
}
Bytes slice(const Bytes &bytes,size_t begin,size_t end) {
    return Bytes(bytes.begin()+begin,bytes.begin()+end);
}
void ipv4_fragment_udp_tcp_and_duplicate() {
    Fixture udp;auto bytes=transport_payload();
    auto tail=fragment(slice(bytes,16,bytes.size()),16,false);
    auto head=fragment(slice(bytes,0,16),0,true);
    udp.feed(tail);udp.feed(tail);
    require(udp.messages.empty(),"IPv4 尾片或重复尾片被提前交付");
    udp.feed(head);udp.check(1);
    require(udp.messages[0]["ip_reassembled"]==true && udp.messages[0]["ip_fragment_count"]==2,
            "IPv4 重组来源标记或去重计数错误");
    require(udp.processor.active_fragment_datagrams()==0 && udp.processor.fragment_buffered_bytes()==0,
            "IPv4 重组完成未释放缓存");
    require(udp.errors.empty(),"合法乱序 IPv4 被误报异常");
    Fixture tcp;tcp.handshake();bytes=transport_payload(true);
    tail=fragment(slice(bytes,24,bytes.size()),24,false,100,6);
    head=fragment(slice(bytes,0,24),0,true,100,6);
    tcp.feed(tail);tcp.feed(head);tcp.check(1);
    require(tcp.messages[0]["ip_reassembled"]==true && tcp.errors.empty(),"IPv4 分片 TCP 未接入流重组");
}
void ipv4_fragment_keys_and_overlap_isolation() {
    Fixture f;auto bytes=transport_payload();
    auto a=fragment(slice(bytes,0,16),0,true,101,17,false,7);
    auto b=fragment(slice(bytes,16,bytes.size()),16,false,101,17,false,8);
    f.feed(a);f.feed(b);require(f.messages.empty(),"不同 VLAN 的 IPv4 分片被混合");
    auto c=fragment(slice(bytes,16,bytes.size()),16,false,101,17,false,7);f.feed(c);f.check(1);
    auto d=fragment(slice(bytes,0,16),0,true,101,17,false,8);f.feed(d);f.check(2);
    auto head=fragment(slice(bytes,0,16),0,true,102);f.feed(head);
    auto overlap=fragment(slice(bytes,8,bytes.size()),8,false,102);f.feed(overlap);
    auto tail=fragment(slice(bytes,16,bytes.size()),16,false,102);f.feed(tail);f.check(2);
    require(f.errors.size()==1 && f.processor.fragment_buffered_bytes()==0,
            "IPv4 重叠没有隔离或未释放数据");
    head=fragment(slice(bytes,0,16),0,true,103);tail=fragment(slice(bytes,16,bytes.size()),16,false,103);
    f.feed(head);f.feed(tail);f.check(3); // 一个坏数据报不能破坏其他标识的数据报。
    std::vector<std::string> errors;
    IPv4Fragments separate([&](const std::exception &e){errors.push_back(e.what());});
    auto direct=fragment(slice(bytes,0,16),0,true,104);
    auto reverse=fragment(slice(bytes,16,bytes.size()),16,false,104,17,true);
    require(separate.process(direct).pending && separate.process(reverse).pending,
            "反向 IPv4 地址键被混合");
    auto other_protocol=fragment(slice(bytes,16,bytes.size()),16,false,104,6);
    require(separate.process(other_protocol).pending && separate.pending()==3,
            "不同 IP 协议的同标识分片被混合");
}
void ipv4_fragment_timeout_and_resource_limits() {
    std::vector<std::string> errors;
    IPv4Fragments fragments([&](const std::exception &e){errors.push_back(e.what());});
    auto now=std::chrono::steady_clock::time_point{};
    auto bytes=transport_payload();auto head=fragment(slice(bytes,0,16),0,true);
    fragments.process(head,now);
    fragments.expire(now+std::chrono::seconds(29));require(fragments.pending()==1,"IPv4 分片提前过期");
    fragments.expire(now+std::chrono::seconds(30));
    require(fragments.pending()==0 && fragments.buffered_bytes()==0 && errors.size()==1,"IPv4 分片过期未释放并报错");
    for(uint16_t i=0;i<128;++i){auto p=fragment(Bytes(8),0,true,i);fragments.process(p,now);}
    bool rejected=false;
    try {auto p=fragment(Bytes(8),0,true,128);fragments.process(p,now);}
    catch(const CaptureLimitError &){rejected=true;}
    require(rejected && fragments.pending()==128,"IPv4 数据报数量配额未执行");
    IPv4Fragments pieces([](const std::exception &){});
    for(uint32_t i=0;i<256;++i){auto p=fragment(Bytes(8),i*8,true);pieces.process(p,now);}
    rejected=false;
    try {auto p=fragment(Bytes(8),256*8,true);pieces.process(p,now);}
    catch(const CaptureLimitError &){rejected=true;}
    require(rejected,"IPv4 每数据报分片数量配额未执行");
    IPv4Fragments budget([](const std::exception &){});
    for(uint16_t i=0;i<64;++i){auto p=fragment(Bytes(65504),0,true,i);budget.process(p,now);}
    rejected=false;
    try {auto p=fragment(Bytes(65504),0,true,64);budget.process(p,now);}
    catch(const CaptureLimitError &){rejected=true;}
    require(rejected && budget.buffered_bytes()<=4*1024*1024,"IPv4 缓存字节配额未执行");
}
void ipv4_fragment_invalid_and_conflicting_input() {
    std::vector<std::string> errors;
    IPv4Fragments fragments([&](const std::exception &e){errors.push_back(e.what());});
    auto bytes=transport_payload();
    fragment_stage("验证非末片的非法对齐长度");
    auto bad_length=fragment(Bytes(7),0,true,110);fragments.process(bad_length);
    fragment_stage("验证超出 IPv4 数据报边界的片段偏移");
    auto bad_offset=fragment(Bytes(8),65528,false,111);fragments.process(bad_offset);
    fragment_stage("验证重复片段内容冲突");
    auto head=fragment(slice(bytes,0,16),0,true,112);fragments.process(head);
    auto altered=slice(bytes,0,16);altered[8]^=1;
    auto conflict=fragment(altered,0,true,112);fragments.process(conflict);
    fragment_stage("验证相互矛盾的末片长度");
    auto tail=fragment(slice(bytes,16,bytes.size()),16,false,113);fragments.process(tail);
    auto inconsistent_tail=fragment(Bytes(12),24,false,113);fragments.process(inconsistent_tail);
    require(errors.size()==4 && fragments.buffered_bytes()==0,"非法分片未隔离或残留缓存");
    fragment_stage("验证重复首片的头长度冲突");
    head=fragment(slice(bytes,0,16),0,true,114);fragments.process(head);
    auto changed_header=fragment(slice(bytes,0,16),0,true,114);
    // 四个 NOP 填满选项区，独立验证 IHL 冲突；单 NOP 的 EOL 补零解析限制另列门禁。
    for(size_t i=0;i<4;++i)changed_header.rfind_pdu<IP>().noop();
    // 按网卡路径重新解析序列化帧，IHL 才具有真实 on-wire 值。
    auto raw=changed_header.serialize();
    fragment_stage("重新解析含 IPv4 选项的首片夹具");
    std::cout << Json{{"operation","test.capture.fragment.fixture"},{"frame_hex",hex(raw)}}.dump() << std::endl;
    EthernetII parsed(raw.data(),raw.size());
    fragment_stage("含选项首片解析成功，验证生产重组入口");
    require(parsed.rfind_pdu<IP>().head_len()==6,"含选项夹具没有产生真实的 24 字节 IPv4 头");
    fragments.process(parsed);
    require(errors.size()==5 && fragments.buffered_bytes()==0,"冲突 IPv4 首片头长度未拒绝");
}
void udp_vlan_and_ipv6() {
    Fixture f;
    Bytes doubled = golden;doubled.insert(doubled.end(), golden.begin(), golden.end());
    auto vlan = EthernetII("00:00:00:00:00:01", "00:00:00:00:00:02") / Dot1Q(7) /
        IP("10.77.0.1", "10.77.0.2") / UDP(30608, 41000) / RawPDU(doubled);
    f.feed(vlan);
    auto ipv6 = EthernetII("00:00:00:00:00:01", "00:00:00:00:00:02") /
        IPv6("fd77::1", "fd77::2") / UDP(30608, 41001) / RawPDU(golden);
    f.feed(ipv6);f.check(3);
    require(f.messages[0]["destination"] == "10.77.0.1:30608", "VLAN 目的地址丢失");
    require(f.messages[2]["source"] == "[fd77::2]:41001", "IPv6 源端点错误");
    require(f.errors.empty(), "正常 UDP 被误判异常");
}
void tcp_reorder_retransmit_wrap(bool ipv6, uint32_t initial) {
    Fixture f;f.handshake(initial, ipv6);
    auto start = initial + 1;
    f.tcp(start + 8, 701, TCP::ACK | TCP::PSH, part(8, 20), false, ipv6);
    require(f.messages.empty(), "TCP 乱序尾段不应提前交付");
    f.tcp(start, 701, TCP::ACK | TCP::PSH, part(0, 8), false, ipv6);
    f.check(1);
    f.tcp(start, 701, TCP::ACK | TCP::PSH, golden, false, ipv6);
    f.check(1); // 重传不能重复计数。
    Bytes joined = golden;joined.insert(joined.end(), golden.begin(), golden.end());
    f.tcp(start + 20, 701, TCP::ACK | TCP::PSH, joined, false, ipv6);
    f.tcp(701, start + 60, TCP::ACK | TCP::PSH, golden, true, ipv6);
    f.check(4);
    require(f.messages.back()["source"] == (ipv6 ? "[fd77::1]:30608" : "10.77.0.1:30608"), "双向 TCP 源端点错误");
    require(f.errors.empty(), "正常重组被误判异常");
    f.tcp(start + 60, 721, TCP::RST | TCP::ACK, {}, false, ipv6);
    require(f.processor.active_streams() == 0, "RST 后未释放 TCP 流");
    f.handshake(2000, ipv6); // 同四元组重新握手，不能继承旧序号和缓冲。
    f.tcp(2001, 701, TCP::ACK | TCP::PSH, golden, false, ipv6);
    f.check(5);
}
void isolated_bad_stream_and_limit() {
    Fixture f;f.handshake();
    auto bad = golden;bad[4] = bad[5] = bad[6] = bad[7] = 0;
    f.tcp(101, 701, TCP::ACK | TCP::PSH, bad);
    require(f.errors.size() == 1, "畸形 TCP 未记录错误");
    f.handshake(300, false, 41001);
    f.tcp(301, 701, TCP::ACK | TCP::PSH, golden, false, false, 41001);
    f.check(1);
    Fixture limited;
    for (uint16_t i = 0; i < 128; ++i) limited.tcp(100, 0, TCP::SYN, {}, false, false, 42000 + i);
    require(limited.processor.active_streams() == 128, "TCP 流计数错误");
    bool rejected = false;
    try { limited.tcp(100, 0, TCP::SYN, {}, false, false, 43000); }
    catch (const CaptureLimitError &) { rejected = true; }
    require(rejected, "超过 TCP 流限额未拒绝");
}
void tcp_partial_and_truncated_close() {
    Fixture partial;
    partial.tcp(500, 701, TCP::ACK | TCP::PSH, golden);
    partial.check(1);
    require(partial.messages[0]["tcp_partial"] == true, "中途捕获缺少 partial 标记");
    Fixture truncated;truncated.handshake();
    truncated.tcp(101, 701, TCP::ACK | TCP::PSH, part(0, 10));
    truncated.tcp(111, 701, TCP::RST | TCP::ACK);
    require(truncated.errors.size() == 1 && truncated.processor.active_streams() == 0,
            "半帧关闭未报告截断或未释放缓存");
    require(truncated.messages.empty(), "半帧被伪装为完整报文");
}
}
int main() {
    try {
        udp_vlan_and_ipv6();
        tcp_reorder_retransmit_wrap(false, 100);
        tcp_reorder_retransmit_wrap(true, 0xfffffff8);
        isolated_bad_stream_and_limit();
        tcp_partial_and_truncated_close();
        ipv4_fragment_udp_tcp_and_duplicate();
        ipv4_fragment_keys_and_overlap_isolation();
        ipv4_fragment_timeout_and_resource_limits();
        ipv4_fragment_invalid_and_conflicting_input();
        std::cout << Json{{"message", "原生抓包 IPv4/IPv6/VLAN、双向 TCP 重组及 IPv4 分片乱序、重复、隔离、过期与资源限额测试通过"}}.dump() << std::endl;
    } catch (const std::exception &error) { log_error("test.capture", error);return 1; }
    return 0;
}
