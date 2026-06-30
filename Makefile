.PHONY: dev tile-server

dev:
	@trap 'kill $$(jobs -p) 2>/dev/null' EXIT; \
	uvicorn tile_server:app --host 0.0.0.0 --port 8502 --reload & \
	streamlit run app.py

tile-server:
	uvicorn tile_server:app --host 0.0.0.0 --port 8502 --reload
