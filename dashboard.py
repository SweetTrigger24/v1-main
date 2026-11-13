"""Lối vào tương thích: python dashboard.py khởi chạy dashboard Reflex."""
import subprocess
import sys
import os

from common import BASE_DIR

if __name__ == "__main__":
    os.environ.setdefault("REFLEX_SSR", "false")
    raise SystemExit(subprocess.call([sys.executable, "-m", "reflex", "run", "--env", "prod", "--single-port",
                                     "--backend-host", "0.0.0.0", "--backend-port", "3000"], cwd=BASE_DIR))
