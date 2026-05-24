# We-MP-RSS Docker Deployment Guide (Server Quick Start)

This README only keeps Docker deployment and operations steps.

## 1. Prerequisites

Install on server:
- Docker
- Docker Compose (`docker compose` command available)

## 2. Clone Repository

```bash
git clone https://github.com/cjx6668845/we-mp-rss.git we-mp-rss-md
cd we-mp-rss-md
```

## 3. Prepare Persistent Data Directory

```bash
mkdir -p /data/we-mp-rss
```

The container path `/app/data` will be mapped to host `/data/we-mp-rss`.

## 4. Build Image

```bash
docker build -t we-mp-rss:markitdown .
```

## 5. Start Service (Recommended)

```bash
WE_MPRSS_DATA_DIR=/data/we-mp-rss \
docker compose -f compose/docker-compose-sqlite.yaml up -d --force-recreate
```

Access:
- `http://<server-ip>:8001`

Default login (change in compose env if needed):
- Username: `admin`
- Password: `admin@123`

## 6. Check Status and Logs

```bash
docker compose -f compose/docker-compose-sqlite.yaml ps
```

```bash
docker compose -f compose/docker-compose-sqlite.yaml logs -f we-mp-rss
```

## 7. Markdown Export Output

Export path:
- Host: `/data/we-mp-rss/markdown`

Naming:
- `mpName_date_articleTitle.md`
- Images in matching `_images` folders

## 8. Update Deployment

```bash
git pull
docker build -t we-mp-rss:markitdown .
WE_MPRSS_DATA_DIR=/data/we-mp-rss \
docker compose -f compose/docker-compose-sqlite.yaml up -d --force-recreate
```

## 9. Restart / Stop

Restart:

```bash
docker compose -f compose/docker-compose-sqlite.yaml restart we-mp-rss
```

Stop and remove containers (data in `/data/we-mp-rss` is kept):

```bash
docker compose -f compose/docker-compose-sqlite.yaml down
```

## 10. Common Pitfall

If you run commands in `tools/`, `docker build -t xxx .` will fail because `tools/` has no `Dockerfile`.

Run from project root (`we-mp-rss-md/`) or use:

```bash
docker build -t we-mp-rss:markitdown -f ../Dockerfile ..
WE_MPRSS_DATA_DIR=/data/we-mp-rss \
docker compose -f ../compose/docker-compose-sqlite.yaml up -d --force-recreate
```
