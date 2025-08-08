# 使用 Python 3.11-slim 基础镜像
FROM python:3.11-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    build-essential \
    default-libmysqlclient-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 拷贝依赖文件并安装
COPY requirements.txt ./
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt


# 拷贝所有代码
COPY . .

# 默认启动命令（也会被 docker-compose 覆盖）
CMD ["python3", "main.py"]
