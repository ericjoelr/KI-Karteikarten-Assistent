.PHONY: run mlx

# Starts backend and frontend in one terminal; Ctrl+C stops both.
# `kill 0` ends the whole process group, so the backend never outlives the frontend.
run:
	@trap 'kill 0' EXIT; \
	uv run uvicorn backend.main:app & \
	uv run streamlit run src/frontend/streamlit_app.py

# Optional, Apple Silicon only: the fine-tuned local model (~5 GB).
# The base model is fetched up front, so the first start does not run into the
# server's start timeout; it is the one named in the adapter's run_config.json.
mlx:
	uv sync --extra mlx
	uv run hf download mlx-community/Qwen3-VL-4B-Instruct-8bit
	uv run hf download ericjoelr/qwen3-vl-4b-karteikarten-lora-mlx \
		--local-dir models/qwen3-vl-4b-karteikarten-lora
