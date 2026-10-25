.PHONY: run

# Starts backend and frontend in one terminal; Ctrl+C stops both.
# `kill 0` ends the whole process group, so the backend never outlives the frontend.
run:
	@trap 'kill 0' EXIT; \
	uv run uvicorn backend.main:app & \
	uv run streamlit run src/frontend/streamlit_app.py
