# 多阶段无关，单阶段即可；基于官方 Playwright 镜像自带依赖
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# 系统依赖已在基础镜像中；仅装 Python 依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 暴露仪表盘端口
EXPOSE 5000

# 数据卷：DB / profile / logs 持久化
VOLUME ["/app/browser_profile", "/app/data"]

# 默认启动仪表盘 + 后台监控（无浏览器弹窗，适合容器）
CMD ["python", "app.py", "--no-browser", "--port", "5000"]
