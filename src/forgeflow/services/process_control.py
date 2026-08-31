from __future__ import annotations

import os
import subprocess


def terminate_windows_process_tree(pid: int) -> bool:
    if os.name != "nt" or pid <= 0:
        return False
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def terminate_process_tree(process: subprocess.Popen, *, timeout: float = 5) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        terminate_windows_process_tree(process.pid)
    else:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)
