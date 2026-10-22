FROM node:24-slim
WORKDIR /app
ENV NODE_ENV=production NODE_NO_WARNINGS=1 MEMORY_DIR=/app/.memory LOGS_DIR=/app/logs
COPY package.json ./
COPY engine ./engine
COPY src ./src
EXPOSE 8100
CMD ["node", "--experimental-transform-types", "src/server.ts"]
