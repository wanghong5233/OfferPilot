FROM node:22-alpine AS builder

WORKDIR /app

COPY package*.json /app/
RUN npm ci

COPY . /app

ARG NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8010
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

RUN NEXT_OUTPUT_STANDALONE=1 npx next build --webpack


FROM node:22-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/.next/standalone /app/
COPY --from=builder /app/.next/static /app/.next/static
COPY --from=builder /app/public /app/public

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]
