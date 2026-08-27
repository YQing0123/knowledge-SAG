# ── deps ───────────────────────────────────────────────
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
COPY scripts/require-npm.cjs ./scripts/require-npm.cjs
# Keep image builds deterministic while avoiding audit/network stalls during
# local Docker Desktop rebuilds. The lockfile remains the source of truth.
RUN npm ci --prefer-offline --no-audit --no-fund

# ── build ──────────────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# NEXT_PUBLIC_* 在构建时内联；浏览器直连该地址
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ARG NEXT_PUBLIC_ENABLE_WINDOW_SCALING=true
ENV NEXT_PUBLIC_ENABLE_WINDOW_SCALING=$NEXT_PUBLIC_ENABLE_WINDOW_SCALING
RUN npm run build

# ── runner（standalone）────────────────────────────────
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]

# Docker Desktop fallback target: build the exact same Next standalone output
# on the host, then package only runtime files. Compose keeps using `runner`.
FROM node:20-alpine AS runtime-prebuilt
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001
COPY public ./knowledge-SAG/apps/web/public
COPY --chown=nextjs:nodejs .next/standalone ./
COPY --chown=nextjs:nodejs .next/static ./knowledge-SAG/apps/web/.next/static
USER nextjs
WORKDIR /app/knowledge-SAG/apps/web
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
