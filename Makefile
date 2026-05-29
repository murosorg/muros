# MurOS

.PHONY: help install backend back frontend front clean

help:
	@echo "MurOS - cibles disponibles :"
	@echo "  make install    Installe les dependances (venv Python + npm)"
	@echo "  make backend    Lance le backend (uvicorn, port 8000)"
	@echo "  make frontend   Lance le frontend (vite, port 5173)"
	@echo "  make clean      Supprime venv et node_modules"
	@echo ""
	@echo "Demarrage rapide :"
	@echo "  make install"
	@echo "  make backend   # dans un terminal"
	@echo "  make frontend  # dans un autre terminal"

install: backend/.venv frontend/node_modules

backend/.venv: backend/requirements.txt
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install --upgrade pip
	backend/.venv/bin/pip install -r backend/requirements.txt

frontend/node_modules: frontend/package.json
	cd frontend && npm install

backend: backend/.venv
	cd backend && .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

back: backend

frontend: frontend/node_modules
	cd frontend && npm run dev

front: frontend

clean:
	rm -rf backend/.venv backend/__pycache__ backend/app/__pycache__
	rm -rf frontend/node_modules frontend/dist
