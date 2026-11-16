FROM node:22-alpine

RUN apk add --no-cache bash
WORKDIR /app

COPY packages/auth /packages/auth
COPY uis/talent-pipeline-tracker/package*.json uis/talent-pipeline-tracker/.npmrc ./talent-pipeline-tracker/
RUN cd talent-pipeline-tracker && npm ci
COPY uis/backoffice/package*.json uis/backoffice/.npmrc ./backoffice/
RUN cd backoffice && npm ci

COPY uis/talent-pipeline-tracker ./talent-pipeline-tracker
COPY uis/backoffice ./backoffice
COPY uis/start.sh ./start.sh

EXPOSE 3000 3001
CMD ["bash", "/app/start.sh"]
