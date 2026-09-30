#include "runtime.hpp"
#include "network.hpp"
#include <fstream>
#include <csignal>

int main(int argc, char **argv) {
    try {
        std::string bind="127.0.0.1", catalog, config, name="soa_partner";
        uint16_t port=16789;
        bool network=false;
        for (int i=1;i<argc;++i) {
            std::string arg=argv[i];
            if (arg=="run") continue;
            if (arg=="--network") {network=true;continue;}
            if (arg=="--version") { std::cout << "soa_partner 0.1.0 vsomeip 3.5.10\n"; return 0; }
            if (i+1>=argc) throw std::runtime_error("参数缺少值: "+arg);
            std::string value=argv[++i];
            if (arg=="-p" || arg=="--port") port=agent::bounded_number(agent::Json(value),65535,"port");
            else if (arg=="--bind") bind=value;
            else if (arg=="--catalog" || arg=="-r") catalog=value;
            else if (arg=="--config" || arg=="-c") config=value;
            else if (arg=="--name") name=value;
            else if (arg=="-d") {} // SAT 的域标签由配置文件的网卡映射决定。
            else throw std::runtime_error("未知参数: "+arg);
        }
        if (network) {
            boost::asio::io_context io;
            agent::NetworkRuntime runtime(io);
            auto control=agent::Listener::create(io,bind,port,true,[&](auto conn) {
                conn->handler=[&](const auto &message,auto connection){runtime.control(message,connection);};
            });
            boost::asio::signal_set signals(io,SIGINT,SIGTERM);
            signals.async_wait([&](auto,auto){control->close();runtime.shutdown();io.stop();});
            std::cout<<(agent::Json{{"operation","native.ready"},{"port",control->port()},
                {"message","原生网络监听控制服务已启动"}}.dump()+"\n")<<std::flush;
            io.run();
            return 0;
        }
        if (catalog.empty() || config.empty()) throw std::runtime_error("必须提供 --catalog 与 --config");
#ifdef _WIN32
        _putenv_s("VSOMEIP_CONFIGURATION",config.c_str());
#else
        setenv("VSOMEIP_CONFIGURATION",config.c_str(),1);
#endif
        std::ifstream input(catalog);
        if (!input) throw std::runtime_error("无法打开服务目录: "+catalog);
        agent::Json services; input >> services;
        boost::asio::io_context io;
        agent::Runtime runtime(io,services,bind,name);
        auto control=agent::Listener::create(io,bind,port,true,[&](auto conn) {
            conn->handler=[&](const auto &message, auto connection){runtime.control(message,connection);};
        });
        boost::asio::signal_set signals(io,SIGINT,SIGTERM);
        signals.async_wait([&](auto,auto){control->close();runtime.shutdown();io.stop();});
        // 就绪 JSON 与换行必须在同一次插入中输出，避免协议栈线程在二者之间写日志。
        std::cout << (agent::Json{{"operation","native.ready"},{"port",control->port()},
                                {"message","原生控制服务已启动"}}.dump()+"\n") << std::flush;
        io.run();
    } catch (const std::exception &error) {
        agent::log_error("native.main",error);
        return 1;
    }
    return 0;
}
