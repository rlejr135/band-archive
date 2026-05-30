# Stage 1: Build
FROM node:20-alpine AS build

WORKDIR /app

# 패키지 설치를 위한 파일 복사
COPY package*.json ./
RUN npm install

# 소스 코드 복사 및 빌드
COPY . .
RUN npm run build

# Stage 2: Serve
FROM nginx:stable-alpine

# Nginx 설정 파일 복사
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 빌드된 파일 복사
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
