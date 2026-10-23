DEMO_ARGS ?=

.PHONY: demo
demo:
	@uv run handoff-demo $(DEMO_ARGS)
