"""测试隔离：在导入应用模块之前，把数据目录重定向到临时目录。

避免 `from app import app` 在 pytest 收集阶段打开生产 data/monitor.db，
使测试运行在隔离的临时数据库上。
"""

import atexit
import os
import shutil
import tempfile

# pytest 会先于测试模块导入本 conftest，因此在任何被测模块读取配置前生效。
_tmp_dir = tempfile.mkdtemp(prefix="xianyu-test-")
os.environ["MONITOR_DATA_DIR"] = _tmp_dir
atexit.register(shutil.rmtree, _tmp_dir, ignore_errors=True)
