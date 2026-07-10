FROM alpine:3.20

RUN apk add --no-cache python3 git github-cli

WORKDIR /app
COPY watcher.py index.html entrypoint.sh ./

# 容器内自带 Bong 克隆（volume 持久化），首启由 entrypoint 完成
ENV BONG_REPO=/data/Bong \
    PORT=8901 \
    REFRESH_SEC=300

EXPOSE 8901
VOLUME /data

ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
