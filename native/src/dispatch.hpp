#pragma once
#include <boost/asio.hpp>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>

namespace agent {
// 不把每条重解码任务都 post 到执行器：每次仅执行一条，再让出给控制/写完成回调。
// 保留先进先出与有限输入预算，不丢弃事件、不合并字段、不改变线上发送周期。
class ReceiveDispatcher : public std::enable_shared_from_this<ReceiveDispatcher> {
    struct Item { std::size_t bytes; std::function<void()> handler; };
    boost::asio::io_context &io_;
    std::mutex mutex_;
    std::deque<Item> queue_;
    std::size_t bytes_ = 0;
    bool scheduled_ = false, open_ = true;
    std::string failure_;
    void schedule() {
        auto self=shared_from_this();
        boost::asio::post(io_,[self]{self->drain();});
    }
    void drain() {
        Item item;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if(!open_)return;
            // 输入超限是可见的进程失败，由 native.main 记录完整堆栈并退出；不伪造交付。
            if(!failure_.empty())throw std::runtime_error(failure_);
            item=std::move(queue_.front());queue_.pop_front();bytes_-=item.bytes;
        }
        item.handler();
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if(!open_)return;
            if(queue_.empty() && failure_.empty())scheduled_=false;
            else schedule();
        }
    }
public:
    explicit ReceiveDispatcher(boost::asio::io_context &io) : io_(io) {}
    void submit(std::size_t bytes,std::function<void()> handler) {
        std::lock_guard<std::mutex> lock(mutex_);
        if(!open_ || !failure_.empty())return;
        if(queue_.size()>=1000 || bytes>16*1024*1024 || bytes_>16*1024*1024-bytes) {
            failure_="原生接收任务队列超限，待处理帧="+std::to_string(queue_.size())+
                "，待处理字节="+std::to_string(bytes_)+"，新增字节="+std::to_string(bytes);
        } else {queue_.push_back({bytes,std::move(handler)});bytes_+=bytes;}
        if(!scheduled_) {scheduled_=true;schedule();}
    }
    void stop() {
        std::lock_guard<std::mutex> lock(mutex_);
        open_=false;queue_.clear();bytes_=0;failure_.clear();
    }
};
}
