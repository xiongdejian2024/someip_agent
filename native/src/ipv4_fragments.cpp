#include "ipv4_fragments.hpp"
#include <tins/tins.h>

namespace agent {
void IPv4Fragments::reject(Datagram &datagram,const char *message) {
    bytes_-=datagram.bytes;
    datagram.bytes=0;
    datagram.pieces.clear();
    datagram.reassembler.clear_streams();
    datagram.rejected=true; // 保留隔离键至超时，后续片段不能复活有歧义的数据报。
    ++errors_;
    error_(std::runtime_error(message));
}
void IPv4Fragments::expire(Clock::time_point now) {
    for(auto it=datagrams_.begin();it!=datagrams_.end();) {
        if(now<it->second->expires){++it;continue;}
        bytes_-=it->second->bytes;
        const bool incomplete=!it->second->rejected;
        it=datagrams_.erase(it);
        if(incomplete) {
            ++errors_;
            error_(std::runtime_error("IPv4 分片重组超过 30 秒，丢弃不完整数据报"));
        }
    }
}
void IPv4Fragments::finish() {
    size_t incomplete=0;
    for(const auto &[key,datagram]:datagrams_)if(!datagram->rejected)++incomplete;
    bytes_=0;datagrams_.clear();
    while(incomplete--) {
        ++errors_;
        error_(std::runtime_error("PCAP 文件结束，IPv4 分片不完整"));
    }
}
FragmentResult IPv4Fragments::process(Tins::PDU &packet,Clock::time_point now) {
    expire(now);
    auto ip=packet.find_pdu<Tins::IP>();
    if(!ip || (!ip->fragment_offset() && !(ip->flags() & Tins::IP::MORE_FRAGMENTS)))return {};
    std::vector<uint16_t> vlans;
    for(auto pdu=&packet;pdu && pdu!=ip;pdu=pdu->inner_pdu())
        if(auto vlan=dynamic_cast<Tins::Dot1Q *>(pdu))vlans.push_back(vlan->id());
    Key key{uint32_t(ip->src_addr()),uint32_t(ip->dst_addr()),ip->id(),ip->protocol(),vlans};
    auto found=datagrams_.find(key);
    if(found==datagrams_.end()) {
        if(datagrams_.size()>=128)throw CaptureLimitError("IPv4 待重组数据报超过 128，停止本捕获");
        auto datagram=std::make_unique<Datagram>();
        datagram->expires=now+std::chrono::seconds(30);
        found=datagrams_.emplace(key,std::move(datagram)).first;
    }
    auto &datagram=*found->second;
    if(datagram.rejected)return {true,false,0};
    const uint32_t start=uint32_t(ip->fragment_offset())*8;
    auto payload=ip->inner_pdu() ? ip->inner_pdu()->serialize() : Bytes{};
    const uint32_t end=start+payload.size();
    const bool more=ip->flags() & Tins::IP::MORE_FRAGMENTS;
    if(!start) {
        auto header_size=uint32_t(ip->head_len())*4;
        if(datagram.first_header_size && *datagram.first_header_size!=header_size) {
            reject(datagram,"重复 IPv4 首片的头长度冲突");return {true,false,0};
        }
        datagram.first_header_size=header_size;
    }
    auto max_payload=65535-datagram.first_header_size.value_or(20);
    if(payload.empty() || end>max_payload || (more && payload.size()%8) ||
        (ip->flags() & Tins::IP::DONT_FRAGMENT) ||
        (datagram.final_size && *datagram.final_size>max_payload)) {
        reject(datagram,"IPv4 分片长度、偏移或非末片对齐非法");return {true,false,0};
    }
    if(datagram.final_size && (end>*datagram.final_size || (!more && end!=*datagram.final_size))) {
        reject(datagram,"IPv4 分片末尾长度矛盾");return {true,false,0};
    }
    for(const auto &[offset,body]:datagram.pieces) {
        if(offset+body.size()>max_payload) {
            reject(datagram,"IPv4 首片头与此前接收的片段长度冲突");return {true,false,0};
        }
        if(start==offset && payload==body) {
            if(more != (!datagram.final_size || end!=*datagram.final_size)) {
                reject(datagram,"重复 IPv4 分片的末片标志矛盾");
            }
            return {true,false,0};
        }
        if(start<offset+body.size() && offset<end) {
            reject(datagram,"IPv4 分片重叠或重复片段内容冲突，隔离此数据报");return {true,false,0};
        }
        if(!more && offset+body.size()>end) {
            reject(datagram,"IPv4 末片早于已接收的片段末尾");return {true,false,0};
        }
    }
    if(datagram.pieces.size()>=256 || bytes_+payload.size()>4*1024*1024)
        throw CaptureLimitError("IPv4 分片数量或 4MiB 总缓存配额超限，停止本捕获");
    if(!more)datagram.final_size=end;
    datagram.bytes+=payload.size();bytes_+=payload.size();
    datagram.pieces.emplace(start,std::move(payload));
    try {
        if(datagram.reassembler.process(packet)==Tins::IPv4Reassembler::REASSEMBLED) {
            size_t count=datagram.pieces.size();
            bytes_-=datagram.bytes;datagrams_.erase(found);++reassembled_;
            return {false,true,count};
        }
    } catch(const std::exception &error) {
        reject(datagram,"libtins IPv4 重组失败，隔离此数据报");
        error_(error); // 保留库抛出的原始异常和调用栈，不只记录包装错误。
    }
    return {true,false,0};
}
}
