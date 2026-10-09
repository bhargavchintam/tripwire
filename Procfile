checkpoint: uv run uvicorn checkpoint.app:app --host ${CHECKPOINT_HOST:-127.0.0.1} --port 8000
detector: sh -c 'if [ -f detection/loop.py ]; then exec uv run python -m detection.loop --rules secret_theft,baseline_novelty; else echo "detector: detection/loop.py not merged yet (Sripadha) - idling"; while true; do sleep 3600; done; fi'
web: cd web && npm run dev
