FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src
ENV HOST=0.0.0.0 DATA_DIR=/data
VOLUME /data
EXPOSE 3000
CMD ["node", "src/index.js"]
