import sys
import os

# 将当前目录添加到 Python 路径中
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.app import app

if __name__ == "__main__":
    app.run()
