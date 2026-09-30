#pragma once
#include <boost/asio.hpp>
#include <nlohmann/json.hpp>
#include <deque>
#include <functional>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#ifndef _WIN32
#include <execinfo.h>
#else
#include <windows.h>
#endif

namespace agent {
using Json = nlohmann::json;
using Tcp = boost::asio::ip::tcp;
constexpr std::size_t max_frame = 4 * 1024 * 1024;

inline void log_error(const std::string &operation, const std::exception &error) {
    Json stack=Json::array();
#ifndef _WIN32
    void *frames[32];
    int count=backtrace(frames,32);
    char **symbols=backtrace_symbols(frames,count);
    if(symbols) {for(int i=0;i<count;++i)stack.push_back(symbols[i]);std::free(symbols);}
#else
    void *frames[32];
    auto count=CaptureStackBackTrace(0,32,frames,nullptr);
    for(unsigned short i=0;i<count;++i){std::ostringstream address;address<<frames[i];stack.push_back(address.str());}
#endif
    std::cerr << Json{{"level", "ERROR"}, {"operation", operation},
                     {"message", error.what()},{"exception_stack",stack}}.dump() << std::endl;
}

// 控制通道保持 SAT 的长度帧，成员通道保持连续 JSON 文档（不依赖 TCP 包边界）。
class Connection : public std::enable_shared_from_this<Connection> {
public:
    Tcp::socket socket;
    bool framed;
    std::function<void(const Json &, std::shared_ptr<Connection>)> handler;
    std::function<void()> on_close;
    Connection(boost::asio::io_context &io, bool control) : socket(io), framed(control) {}
    void start() { read(); }
    void send(const Json &message) {
        std::string body = message.dump(-1, ' ', true);
        if(body.size()>max_frame) {
            log_error("ipc.send",std::runtime_error("IPC 输出帧大小超限"));
            auto self=shared_from_this();
            boost::asio::post(socket.get_executor(),[self]{self->close();});
            return;
        }
        if (framed) {
            std::ostringstream prefix;
            prefix << std::hex << std::setw(8) << std::setfill('0') << body.size();
            body = prefix.str() + body;
        }
        auto self = shared_from_this();
        boost::asio::post(socket.get_executor(), [self, body = std::move(body)] {
            if (!self->socket.is_open()) return;
            if (self->out_.size() >= 1000 || self->out_bytes_ + body.size() > 16 * 1024 * 1024) {
                log_error("ipc.backpressure",std::runtime_error("IPC 慢消费者队列超限"));
                self->close(); // 慢消费者不能无限占用原生数据面内存。
                return;
            }
            const bool idle = self->out_.empty();
            self->out_bytes_ += body.size();
            self->out_.push_back(body);
            if (idle) self->write();
        });
    }
    void close() {
        boost::system::error_code ignored;
        socket.close(ignored);
        if (on_close) { auto callback = std::move(on_close); callback(); }
    }
private:
    std::array<char, 8192> buffer_{};
    std::string input_;
    std::deque<std::string> out_;
    size_t out_bytes_ = 0;
    void read() {
        auto self = shared_from_this();
        socket.async_read_some(boost::asio::buffer(buffer_), [self](auto ec, auto count) {
            if (ec) { self->close(); return; }
            try {
                self->input_.append(self->buffer_.data(), count);
                if (self->input_.size() > max_frame + 8) throw std::runtime_error("IPC 缓冲超限");
                self->consume();
                self->read();
            } catch (const std::exception &error) {
                log_error("ipc.read", error);
                self->send({{"action", "error"}, {"error", error.what()}});
                self->input_.clear();
                self->read();
            }
        });
    }
    void consume() {
        while (!input_.empty()) {
            std::size_t offset = 0, length = 0;
            if (framed) {
                if (input_.size() < 8) return;
                if (input_.substr(0,8).find_first_not_of("0123456789abcdefABCDEF") != std::string::npos)
                    throw std::runtime_error("IPC 长度头非法");
                length = std::stoul(input_.substr(0,8), nullptr, 16);
                if (length == 0 || length > max_frame) throw std::runtime_error("IPC 帧大小非法");
                offset = 8;
                if (input_.size() < offset + length) return;
            } else {
                std::size_t begin = input_.find_first_not_of(" \r\n\t");
                if (begin == std::string::npos) { input_.clear(); return; }
                input_.erase(0, begin);
                if (input_[0] != '{') throw std::runtime_error("成员消息必须是 JSON 对象");
                int depth = 0;
                bool string = false, escape = false;
                for (std::size_t i = 0; i < input_.size(); ++i) {
                    char ch = input_[i];
                    if (string) {
                        if (escape) escape = false;
                        else if (ch == '\\') escape = true;
                        else if (ch == '"') string = false;
                    } else if (ch == '"') string = true;
                    else if (ch == '{' || ch == '[') ++depth;
                    else if (ch == '}' || ch == ']') {
                        if (--depth == 0) { length = i + 1; break; }
                    }
                }
                if (length == 0) return;
            }
            auto body = input_.substr(offset, length);
            input_.erase(0, offset + length);
            auto message = Json::parse(body);
            if (!message.is_object()) throw std::runtime_error("IPC 消息必须是对象");
            handler(message, shared_from_this());
        }
    }
    void write() {
        auto self = shared_from_this();
        boost::asio::async_write(socket, boost::asio::buffer(out_.front()), [self](auto ec, auto) {
            if (ec) { self->close(); return; }
            self->out_bytes_ -= self->out_.front().size();
            self->out_.pop_front();
            if (!self->out_.empty()) self->write();
        });
    }
};

class Listener : public std::enable_shared_from_this<Listener> {
    boost::asio::io_context &io_;
    Tcp::acceptor acceptor_;
    bool framed_;
    std::function<void(std::shared_ptr<Connection>)> connected_;
public:
    Listener(boost::asio::io_context &io, const std::string &host, uint16_t port, bool framed,
             std::function<void(std::shared_ptr<Connection>)> connected)
        : io_(io), acceptor_(io, Tcp::endpoint(boost::asio::ip::make_address(host), port)),
          framed_(framed), connected_(std::move(connected)) {}
    static std::shared_ptr<Listener> create(boost::asio::io_context &io, const std::string &host,
             uint16_t port, bool framed, std::function<void(std::shared_ptr<Connection>)> connected) {
        auto listener=std::make_shared<Listener>(io,host,port,framed,std::move(connected));
        listener->accept();return listener;
    }
    uint16_t port() const { return acceptor_.local_endpoint().port(); }
    void close() { boost::system::error_code ec; acceptor_.close(ec); }
private:
    void accept() {
        auto conn = std::make_shared<Connection>(io_, framed_);
        auto self=shared_from_this();
        acceptor_.async_accept(conn->socket, [self, conn](auto ec) {
            if (!ec) { self->connected_(conn); conn->start(); }
            if (self->acceptor_.is_open()) self->accept();
        });
    }
};
inline Json unpack_json(const Json &value) {
    if (value.is_string()) {
        if (value.get<std::string>().empty()) return Json::object();
        return Json::parse(value.get<std::string>());
    }
    return value.is_null() ? Json::object() : value;
}
}
