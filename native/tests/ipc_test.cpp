#include "ipc.hpp"

using namespace agent;
int main() {
    try {
        for(auto framed:{false,true}) {
            boost::asio::io_context io,peer_io;
            Tcp::acceptor acceptor(io,Tcp::endpoint(boost::asio::ip::address_v4::loopback(),0));
            auto connection=std::make_shared<Connection>(io,framed);
            Tcp::socket peer(peer_io);peer.connect(acceptor.local_endpoint());
            acceptor.accept(connection->socket);
            std::vector<Json> messages;
            for(size_t i=0;i<16;++i)messages.push_back(Json{{"index",i},{"data",std::string(512,'x')}});
            boost::asio::post(io,[&]{
                size_t expected=0;
                for(const auto &message:messages) {
                    expected+=message.dump().size()+(framed?8:0);
                    connection->send(message);
                }
                // 首次 async_write 的完成回调不会内联；当前 executor 内应已登记全部帧。
                // 原 post 实现会得到 0，且把尚未登记的帧积压在无预算的任务队列里。
                if(connection->queued_output_bytes()!=expected)
                    throw std::runtime_error("IPC 输出帧没有在当前 executor 内及时登记预算");
            });
            io.run();
            if(connection->queued_output_bytes()!=0)throw std::runtime_error("IPC 输出队列未排空");
            for(const auto &message:messages) {
                auto body=message.dump();
                if(framed) {
                    std::array<char,8> header{};
                    boost::asio::read(peer,boost::asio::buffer(header));
                    if(std::stoul(std::string(header.data(),8),nullptr,16)!=body.size())
                        throw std::runtime_error("IPC 长度帧顺序或长度错误");
                }
                std::string actual(body.size(),'\0');
                boost::asio::read(peer,boost::asio::buffer(actual));
                if(actual!=body)throw std::runtime_error("IPC 输出文档顺序或内容错误");
            }
            connection->close();peer.close();
        }
        std::cout<<Json{{"message","IPC executor 预算登记、连续文档/长度帧和有序排空通过"}}.dump()<<std::endl;
    }catch(const std::exception &error){log_error("test.ipc",error);return 1;}
    return 0;
}
