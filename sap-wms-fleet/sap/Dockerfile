FROM node:24-bookworm-slim

WORKDIR /app
COPY server.js ./

ENV NODE_ENV=production
EXPOSE 4004

CMD ["node", "server.js"]
