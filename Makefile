# OfferPilot — 全 WSL 架构
# 所有服务运行在 WSL (Ubuntu) 中

WSL = wsl -d Ubuntu -e bash -lc
PROJECT = /mnt/e/0找工作/0大模型全栈知识库/OfferPilot

.PHONY: setup boss-login start start-pg start-backend start-frontend ps health

# 一次性初始化
setup:
	$(WSL) "$(PROJECT)/scripts/setup.sh"

# 首次登录 BOSS 直聘（打开浏览器扫码，登录后关闭窗口保存 cookie）
boss-login:
	$(WSL) "$(PROJECT)/scripts/boss-login.sh"

# 一键启动所有服务（PG + Backend + Frontend），Ctrl+C 全部停止
start:
	$(WSL) "$(PROJECT)/scripts/start.sh"

# 单独启动
start-pg:
	$(WSL) "$(PROJECT)/scripts/start.sh pg"

start-backend:
	$(WSL) "$(PROJECT)/scripts/start.sh backend"

start-frontend:
	$(WSL) "$(PROJECT)/scripts/start.sh frontend"

# 状态检查
ps:
	$(WSL) "pg_lsclusters; echo '---'; curl -s -o /dev/null -w 'backend: %%{http_code}\n' http://127.0.0.1:8010/docs || echo 'backend: DOWN'; curl -s -o /dev/null -w 'frontend: %%{http_code}\n' http://127.0.0.1:3000 || echo 'frontend: DOWN'"

health:
	$(WSL) "curl -s http://127.0.0.1:8010/docs > /dev/null && echo 'Backend: OK' || echo 'Backend: DOWN'; curl -s http://127.0.0.1:3000 > /dev/null && echo 'Frontend: OK' || echo 'Frontend: DOWN'"
