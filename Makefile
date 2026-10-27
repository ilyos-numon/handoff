DEMO_ARGS ?=

WEB_SRC := $(shell find web/src web/public -type f) web/index.html web/package.json web/tsconfig.json web/vite.config.ts

.PHONY: web demo
web: web/dist/index.html

web/node_modules/.package-lock.json: web/package-lock.json
	cd web && npm ci

web/dist/index.html: web/node_modules/.package-lock.json $(WEB_SRC)
	cd web && npm run build

demo: web
	@uv run handoff-demo $(DEMO_ARGS)
