"""
Clean old Reqable capture files.

Default mode is a dry run. Add --delete to actually remove files.
"""

import argparse
import os
import time
from pathlib import Path

from reqable_token import find_reqable_capture_dir


def collect_old_files(capture_dir, keep_hours):
    cutoff = time.time() - keep_hours * 3600
    files = []
    total_size = 0
    with os.scandir(capture_dir) as entries:
        for entry in entries:
            if not entry.is_file() or not entry.name.endswith(".reqable"):
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_mtime >= cutoff:
                continue
            files.append((entry.path, stat.st_size, stat.st_mtime))
            total_size += stat.st_size
    return files, total_size


def main():
    parser = argparse.ArgumentParser(description="Clean old Reqable .reqable capture files.")
    parser.add_argument("--keep-hours", type=float, default=24, help="Keep files newer than this many hours.")
    parser.add_argument("--delete", action="store_true", help="Actually delete files. Omit for dry run.")
    args = parser.parse_args()

    capture_dir = find_reqable_capture_dir()
    if not capture_dir:
        print("[X] 找不到 Reqable 抓包目录")
        return

    files, total_size = collect_old_files(capture_dir, args.keep_hours)
    print(f"抓包目录: {capture_dir}")
    print(f"保留最近: {args.keep_hours:g} 小时")
    print(f"待清理文件: {len(files)} 个")
    print(f"可释放空间: {total_size / 1024 / 1024:.2f} MB")

    if not args.delete:
        print("当前是预览模式，没有删除文件。确认后运行:")
        print(f"python cleanup_reqable_capture.py --keep-hours {args.keep_hours:g} --delete")
        return

    deleted = 0
    failed = 0
    for path, _, _ in files:
        try:
            Path(path).unlink()
            deleted += 1
        except OSError:
            failed += 1

    print(f"已删除: {deleted} 个")
    if failed:
        print(f"删除失败: {failed} 个，可能正在被 Reqable 占用")


if __name__ == "__main__":
    main()
