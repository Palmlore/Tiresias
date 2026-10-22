FROM node:24-slim
WORKDIR /app
COPY package.json ./
COPY src ./src
ENV NODE_ENV=production
EXPOSE 8100
CMD ["node", "src/server.ts"]
