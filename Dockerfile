# 单阶段；基于官方 Playwright 镜像自带依赖 + Xvfb（虚拟显示，供有头浏览器用）
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# 时区数据：容器内 TZ=Asia/Shanghai 生效必需（缺失时 glibc 解析失败回退 UTC，
# 导致日志/上次检查等时间比北京慢 8 小时）。
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# 系统依赖已在基础镜像中；仅装 Python 依赖。
# PIP_INDEX_URL 允许中国/受限网络用镜像源构建：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple .
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements.txt ./
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt

COPY . .

# 数据目录（启动脚本以 root 修正属主后降权，见 entrypoint.sh）
RUN mkdir -p /app/data /app/browser_profile

# 启动脚本：修正属主 -> 启动 Xvfb 虚拟显示 -> 降权非 root 启动监控
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 暴露仪表盘端口
EXPOSE 5000

# 数据卷：DB / profile / logs 持久化
VOLUME ["/app/browser_profile", "/app/data"]

# 入口以 root 进入（用于修正卷属主），之后降权到非 root pwuser 运行应用进程（S-08）
ENTRYPOINT ["/entrypoint.sh"]