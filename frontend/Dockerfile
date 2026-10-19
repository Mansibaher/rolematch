# RoleMatch web image (Next.js standalone output)
FROM node:22-slim AS deps
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM node:22-slim AS build
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1
ARG ROLEMATCH_BACKEND_URL=http://api:8000
ENV ROLEMATCH_BACKEND_URL=$ROLEMATCH_BACKEND_URL
COPY --from=deps /web/node_modules ./node_modules
COPY frontend ./
RUN npx next build

FROM node:22-slim
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN useradd --create-home --uid 10001 web
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
USER web
EXPOSE 3000
CMD ["node", "server.js"]
