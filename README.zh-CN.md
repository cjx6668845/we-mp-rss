# We-MP-RSS Docker 部署指南（服务器可直接照做）

本文档只保留 Docker 部署与运维步骤。

## 1. 前置条件

在服务器安装：
- Docker
- Docker Compose（`docker compose` 子命令可用）

## 2. 拉代码

```bash
git clone https://github.com/cjx6668845/we-mp-rss.git we-mp-rss-md
cd we-mp-rss-md
```

## 3. 准备数据目录（持久化）

```bash
mkdir -p /data/we-mp-rss
```

说明：容器内 `/app/data` 会映射到宿主机 `/data/we-mp-rss`。

## 4. 构建镜像（包含当前分支改动）

```bash
docker build -t we-mp-rss:markitdown .
```

## 5. 启动服务（推荐）

```bash
WE_MPRSS_DATA_DIR=/data/we-mp-rss \
docker compose -f compose/docker-compose-sqlite.yaml up -d --force-recreate
```

启动后访问：
- `http://<服务器IP>:8001`

默认账号（可在 compose 文件中改环境变量）：
- 用户名：`admin`
- 密码：`admin@123`

## 6. 检查运行状态

```bash
docker compose -f compose/docker-compose-sqlite.yaml ps
```

实时日志：

```bash
docker compose -f compose/docker-compose-sqlite.yaml logs -f we-mp-rss
```

## 7. Markdown 导出位置

文章会自动导出到：
- 宿主机：`/data/we-mp-rss/markdown`

命名规则：
- `公众号_日期_文章名称.md`
- 图片在同名 `_images` 文件夹

## 8. 更新部署

```bash
git pull
docker build -t we-mp-rss:markitdown .
WE_MPRSS_DATA_DIR=/data/we-mp-rss \
docker compose -f compose/docker-compose-sqlite.yaml up -d --force-recreate
```

## 9. 重启 / 停止

重启：

```bash
docker compose -f compose/docker-compose-sqlite.yaml restart we-mp-rss
```

停止并移除容器（不会删除 `/data/we-mp-rss` 数据）：

```bash
docker compose -f compose/docker-compose-sqlite.yaml down
```

## 10. 常见坑

1. 在 `tools/` 目录执行 `docker build -t xxx .` 会报找不到 Dockerfile。
2. 请在项目根目录执行命令（`we-mp-rss-md/`）。
3. 如果必须在 `tools/` 执行：

```bash
docker build -t we-mp-rss:markitdown -f ../Dockerfile ..
WE_MPRSS_DATA_DIR=/data/we-mp-rss \
docker compose -f ../compose/docker-compose-sqlite.yaml up -d --force-recreate
```
