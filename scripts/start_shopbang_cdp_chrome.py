"""启动隔离的本机 Chrome，并开启本地 CDP 调试端口。"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.settings import get_settings


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    settings = get_settings()
    parser = argparse.ArgumentParser(description="启动隔离的本机 Chrome，并开启本地 CDP 调试端口。")
    parser.add_argument(
        "--port",
        type=int,
        default=settings.shopbang_cdp_port,
        help="本地 Chrome 远程调试端口。",
    )
    parser.add_argument(
        "--browser-path",
        type=str,
        default=settings.shopbang_cdp_browser_path,
        help="Chrome 可执行文件路径。",
    )
    parser.add_argument(
        "--user-data-dir",
        type=str,
        default=str(settings.shopbang_cdp_user_data_path),
        help="隔离的 Chrome 用户目录。",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=settings.shopbang_erp_login_url,
        help="启动后默认打开的页面。",
    )
    return parser.parse_args()


def is_local_port_open(port: int) -> bool:
    """检测本机端口是否已被监听。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def build_launch_command(*, browser_path: Path, user_data_dir: Path, port: int, url: str) -> list[str]:
    """构建 Chrome 启动命令。"""

    return [
        str(browser_path),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--new-window",
        url,
    ]


def wait_for_port(port: int, timeout_seconds: float = 10.0) -> bool:
    """等待本机调试端口就绪。"""

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_local_port_open(port):
            return True
        time.sleep(0.2)
    return False


def main() -> None:
    """启动隔离 Chrome，并打印后续采集需要的 CDP 地址。"""

    args = parse_args()
    browser_path = Path(args.browser_path).expanduser()
    user_data_dir = Path(args.user_data_dir).expanduser()
    port = max(int(args.port), 1)
    url = str(args.url or "").strip() or "about:blank"

    if not browser_path.exists():
        raise FileNotFoundError(f"未找到 Chrome 可执行文件: {browser_path}")

    if is_local_port_open(port):
        print("shopbang cdp chrome: already_running")
        print(f"cdp_url: http://127.0.0.1:{port}")
        print(f"user_data_dir: {user_data_dir}")
        return

    user_data_dir.mkdir(parents=True, exist_ok=True)
    command = build_launch_command(
        browser_path=browser_path,
        user_data_dir=user_data_dir,
        port=port,
        url=url,
    )
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    if not wait_for_port(port):
        raise RuntimeError(
            f"Chrome 已尝试启动，但在 {port} 端口上未检测到远程调试服务。"
            " 请检查浏览器是否成功打开。"
        )

    print("shopbang cdp chrome: started")
    print(f"browser_path: {browser_path}")
    print(f"user_data_dir: {user_data_dir}")
    print(f"cdp_url: http://127.0.0.1:{port}")
    print(f"opened_url: {url}")
    print("next_step_env: SHOPBANG_CDP_URL=http://127.0.0.1:%s" % port)


if __name__ == "__main__":
    main()
