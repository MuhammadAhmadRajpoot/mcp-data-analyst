# Task shortcuts. On Windows without make, run the commands shown after each target.
PY ?= python

.PHONY: install lint format test test-integration data serve serve-http agent docker

install:          ## editable install with dev + example extras
	$(PY) -m pip install -e ".[dev,examples]"

lint:             ## ruff lint + format check
	ruff check . && ruff format --check .

format:
	ruff format . && ruff check --fix .

test:             ## offline test suite
	$(PY) -m pytest

test-integration: ## real Groq call (needs GROQ_API_KEY)
	$(PY) -m pytest -m integration

data:             ## regenerate the synthetic demo dataset
	$(PY) scripts/generate_demo_data.py

serve:            ## stdio server over the demo data
	mcp-data-analyst --data-dir data/demo --output-dir output

serve-http:       ## Streamable HTTP server on http://127.0.0.1:8106/mcp
	mcp-data-analyst --transport streamable-http --port 8106 --data-dir data/demo --output-dir output

agent:            ## ask the Groq demo agent a question
	$(PY) examples/groq_agent.py

docker:
	docker build -t mcp-data-analyst . && docker run --rm -p 8106:8106 mcp-data-analyst
