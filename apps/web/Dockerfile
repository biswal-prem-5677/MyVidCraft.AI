# syntax=docker/dockerfile:1

# The web UI as its own image.
#
# Separate from the root Dockerfile rather than a stage inside it: that image is
# `python:slim` and the API and worker share it because they share code. The
# frontend shares neither a runtime nor a dependency set with them, and folding
# Node into the Python image would make every worker deploy carry a toolchain it
# never executes.
#
# `next.config.js` already sets `output: "standalone"`, which is what makes the
# runtime stage small: Next traces the modules actually imported and emits a
# self-contained server, so the 300MB `node_modules` never reaches the final
# image.

FROM node:22-alpine AS deps
WORKDIR /app
# `npm ci` needs both files and installs exactly the lockfile — a plain
# `npm install` could silently resolve a different tree in the image than the
# one that was tested.
COPY package.json package-lock.json ./
RUN npm ci


FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Baked in at build time, not read at runtime. `NEXT_PUBLIC_*` values are
# substituted into the client bundle during `next build`, so setting this in
# compose's `environment:` would do nothing — it has to be a build arg.
#
# The default is `localhost:8000` because this address is resolved by the
# *browser*, not by the container. `http://api:8000` would be the natural guess
# and is wrong: that name only exists on the compose network, and the browser is
# on the host. A real deployment overrides this with its public API URL.
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build


FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Non-root, matching the uid the API image uses.
RUN addgroup -g 10001 -S nodejs \
    && adduser -u 10001 -S next -G nodejs

# The standalone output ships its own minimal server.js. Static assets are not
# traced into it and have to be copied separately, or every page loads with no
# CSS and no client bundle.
COPY --from=builder --chown=next:nodejs /app/.next/standalone ./
COPY --from=builder --chown=next:nodejs /app/.next/static ./.next/static

USER next
EXPOSE 3000

CMD ["node", "server.js"]
