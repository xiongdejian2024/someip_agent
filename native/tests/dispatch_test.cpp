#include "dispatch.hpp"
#include "ipc.hpp"
#include <thread>

using namespace agent;
int main() {
    try {
        {
            boost::asio::io_context io;
            auto dispatcher=std::make_shared<ReceiveDispatcher>(io);
            std::vector<int> observed;
            // 由非执行器线程提交，模拟 vsomeip 回调；不依赖耗时阈值判定公平性。
            std::thread producer([&]{
                for(int i=0;i<32;++i)dispatcher->submit(1024,[&,i]{observed.push_back(i);});
            });
            producer.join();
            boost::asio::post(io,[&]{
                if(observed!=std::vector<int>{0})throw std::runtime_error("接收批次未让出给其他就绪任务");
            });
            io.run();
            for(int i=0;i<32;++i)if(observed.at(i)!=i)throw std::runtime_error("接收任务丢失或顺序错误");
            // 空队列后的下一批仍可启动；退出后不得调用捕获旧 Runtime 的闭包。
            io.restart();dispatcher->submit(1,[&]{observed.push_back(32);});io.run();
            if(observed.size()!=33)throw std::runtime_error("第二批接收任务未启动");
            io.restart();dispatcher->submit(1,[]{throw std::runtime_error("停止后仍执行接收任务");});
            dispatcher->stop();io.run();
        }
        for(auto byte_limit:{false,true}) {
            boost::asio::io_context io;
            auto dispatcher=std::make_shared<ReceiveDispatcher>(io);
            int calls=0;
            for(int i=0;i<(byte_limit?17:1001);++i)
                dispatcher->submit(byte_limit?1024*1024:1,[&]{++calls;});
            bool rejected=false;
            try {io.run();} catch(const std::runtime_error &error) {
                if(std::string(error.what()).find("原生接收任务队列超限")==std::string::npos)throw;
                rejected=true;
                log_error("test.dispatch.expected_overflow",error);
            }
            if(!rejected || calls)throw std::runtime_error("接收任务超限未明确失败或伪造了部分交付");
            dispatcher->stop();
        }
        std::cout<<Json{{"message","原生接收 FIFO、公平让出、退出与双预算门禁通过"}}.dump()<<std::endl;
    }catch(const std::exception &error){log_error("test.dispatch",error);return 1;}
    return 0;
}
