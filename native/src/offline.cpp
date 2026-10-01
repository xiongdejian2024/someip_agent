#include "offline.hpp"
#include <tins/tins.h>
#include <limits>

namespace agent {
OfflineImport::OfflineImport(boost::asio::io_context &io,const std::string &path,
                             std::shared_ptr<Connection> connection,std::function<void()> finished)
    :timer_(io),connection_(connection),finished_(std::move(finished)) {
    if(path.empty() || path.size()>4096)throw std::runtime_error("PCAP 导入文件路径非法");
    char error[PCAP_ERRBUF_SIZE]{};
    handle_.reset(pcap_open_offline_with_tstamp_precision(path.c_str(),PCAP_TSTAMP_PRECISION_NANO,error));
    if(!handle_)throw std::runtime_error("读取 PCAP/PCAPNG 失败: "+std::string(error));
    link_type_=pcap_datalink(handle_.get());
    if(link_type_!=DLT_EN10MB && link_type_!=DLT_RAW && link_type_!=DLT_LINUX_SLL &&
       link_type_!=DLT_IPV4 && link_type_!=DLT_IPV6)
        throw std::runtime_error("不支持的 PCAP 链路类型: "+std::to_string(link_type_));
    processor_=std::make_unique<CaptureProcessor>([this](const Json &message){
        auto packet=message;
        packet["frame_message_index"]=frame_["messages"].size();
        frame_["messages"].push_back(packet);
    },[this](const std::exception &error){this->error(error);});
    std::cout<<Json{{"operation","pcap.open"},{"link_type",link_type_},
        {"message","原生离线捕获已打开，不创建线上监听或发包端点"}}.dump()<<std::endl;
}
void OfflineImport::error(const std::exception &error) {
    log_error("pcap.decode",error);
    if(errors_.size()<100)errors_.push_back("帧 "+std::to_string(number_)+": "+error.what());
}
void OfflineImport::decode(const pcap_pkthdr &header,const uint8_t *data) {
    if(header.caplen!=header.len)throw std::runtime_error("PCAP 帧已被 snaplen 截断");
    std::unique_ptr<Tins::PDU> packet;
    if(link_type_==DLT_EN10MB)packet=std::make_unique<Tins::EthernetII>(data,header.caplen);
    else if(link_type_==DLT_LINUX_SLL)packet=std::make_unique<Tins::SLL>(data,header.caplen);
    else {
        if(!header.caplen)throw std::runtime_error("PCAP 原始 IP 帧为空");
        auto version=data[0]>>4;
        if(version==4 && link_type_!=DLT_IPV6)packet=std::make_unique<Tins::IP>(data,header.caplen);
        else if(version==6 && link_type_!=DLT_IPV4)packet=std::make_unique<Tins::IPv6>(data,header.caplen);
        else throw std::runtime_error("PCAP 原始 IP 版本与链路类型不符");
    }
    if(auto ip=packet->find_pdu<Tins::IP>()) {
        frame_.update({{"source_ip",ip->src_addr().to_string()},{"destination_ip",ip->dst_addr().to_string()},
            {"ip_version",4},{"transport",ip->protocol()==17 ? "udp":ip->protocol()==6 ? "tcp":"other"}});
    } else if(auto ip=packet->find_pdu<Tins::IPv6>()) {
        frame_.update({{"source_ip",ip->src_addr().to_string()},{"destination_ip",ip->dst_addr().to_string()},
            {"ip_version",6},{"transport",packet->find_pdu<Tins::UDP>() ? "udp":packet->find_pdu<Tins::TCP>() ? "tcp":"other"}});
    } else return;
    // 短 UDP 可为 DNS 等无关流量，不把它伪装成截断的 SOME/IP。TCP 短段必须进入重组。
    if(auto udp=packet->find_pdu<Tins::UDP>())
        if(!udp->inner_pdu() || udp->inner_pdu()->size()<16)return;
    processor_->feed(*packet,frame_.at("timestamp_ns").get<int64_t>(),
        std::chrono::steady_clock::time_point(std::chrono::nanoseconds(*end_ns_)));
}
void OfflineImport::poll() {
    if(!active_)return;
    auto connection=connection_.lock();
    if(!connection || !connection->socket.is_open()){close();finished_();return;}
    try {
        for(size_t batch=0;batch<16;++batch) {
            pcap_pkthdr *header=nullptr;const u_char *data=nullptr;
            auto result=pcap_next_ex(handle_.get(),&header,&data);
            if(result==-2){done();return;}
            if(result<0)throw std::runtime_error("PCAP 文件读取中断: "+std::string(pcap_geterr(handle_.get())));
            if(!result)break;
            ++number_;captured_bytes_+=header->caplen;
            if(number_>1000000)throw CaptureLimitError("PCAP 原始帧数超过一百万，停止导入");
            if(header->ts.tv_usec<0 || header->ts.tv_usec>=1000000000 ||
               header->ts.tv_sec>(std::numeric_limits<int64_t>::max()-header->ts.tv_usec)/1000000000LL ||
               header->ts.tv_sec<std::numeric_limits<int64_t>::min()/1000000000LL)
                throw std::runtime_error("PCAP 时间戳超出纳秒整数边界");
            int64_t timestamp=int64_t(header->ts.tv_sec)*1000000000LL+header->ts.tv_usec;
            start_ns_=start_ns_ ? std::min(*start_ns_,timestamp):timestamp;
            end_ns_=end_ns_ ? std::max(*end_ns_,timestamp):timestamp;
            frame_={{"action","pcap_frame"},{"number",number_},{"captured_bytes",header->caplen},
                {"timestamp_ns",timestamp},{"transport","other"},{"messages",Json::array()}};
            try {decode(*header,data);}
            catch(const CaptureLimitError &){throw;}
            catch(const std::exception &error){this->error(error);}
            connection->send(frame_);
        }
        timer_.expires_after(std::chrono::milliseconds(5));
        auto self=shared_from_this();
        timer_.async_wait([self](auto error){if(!error)self->poll();});
    } catch(const std::exception &error) {log_error("pcap.read",error);done(error.what());}
}
void OfflineImport::done(const std::string &failure) {
    processor_->finish();
    Json result={{"action","pcap_done"},{"packet_count",number_},{"captured_bytes",captured_bytes_},
        {"start_ns",start_ns_ ? Json(*start_ns_):Json(nullptr)},{"end_ns",end_ns_ ? Json(*end_ns_):Json(nullptr)},
        {"link_type",link_type_},{"errors",errors_},{"runtime","vsomeip"},
        {"reassembled_datagrams",processor_->reassembled_datagrams()},
        {"fragment_error_count",processor_->fragment_error_count()},{"error",failure}};
    if(auto connection=connection_.lock())connection->send(result);
    std::cout<<Json{{"operation","pcap.finish"},{"packet_count",number_},
        {"message",failure.empty() ? "原生离线捕获读取完成":"原生离线捕获读取失败"}}.dump()<<std::endl;
    close();finished_();
}
void OfflineImport::start() {
    timer_.expires_after(std::chrono::milliseconds(1));
    auto self=shared_from_this();
    timer_.async_wait([self](auto error){if(!error)self->poll();});
}
void OfflineImport::close() {active_=false;timer_.cancel();handle_.reset();processor_.reset();}
}
