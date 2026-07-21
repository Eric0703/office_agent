"""入口:uvicorn 启动 api app(08 §4;FR-12)。"""

import uvicorn

from agent_host.api.app import create_app
from agent_host.config import load_config

# 供 ASGI 服务器/测试直接引用(默认配置;serve 命令走 main() 按 config.yaml 重建)
app = create_app()


def main() -> None:
    """按 config.yaml(缺省用默认)启动 HTTP/WS 服务。"""
    config = load_config("config.yaml")
    uvicorn.run(create_app(config), host=config.server.host, port=config.server.port)


if __name__ == "__main__":
    main()
