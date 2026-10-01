"""独立 onefile 升级器入口，不依赖安装目录中的 Python。"""

from someip_agent.update.worker import main

if __name__ == "__main__":
    main()
