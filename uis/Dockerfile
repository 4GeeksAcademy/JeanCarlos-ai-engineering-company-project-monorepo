FROM node:22-alpine

RUN apk add --no-cache bash
WORKDIR /app

COPY talent-pipeline-tracker/package*.json ./talent-pipeline-tracker/
RUN cd talent-pipeline-tracker && npm ci
COPY backoffice/package*.json ./backoffice/
RUN cd backoffice && npm ci

COPY talent-pipeline-tracker ./talent-pipeline-tracker
COPY backoffice ./backoffice
COPY start.sh ./start.sh

# OWASP A05: Next runs as the image's unprivileged `node` user, not root. The build output folders
# (named volumes in docker-compose.yml) start owned by it.
RUN mkdir -p talent-pipeline-tracker/.next backoffice/.next \
 && chown -R node:node /app/talent-pipeline-tracker /app/backoffice
USER node

EXPOSE 3000 3001
CMD ["bash", "/app/start.sh"]
