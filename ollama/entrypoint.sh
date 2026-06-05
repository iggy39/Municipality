#!/bin/sh
set -eu

ollama serve &
ollama_pid="$!"

until ollama list >/dev/null 2>&1; do
  sleep 1
done

ollama create "${OLLAMA_MODEL}" -f /models/qwen3.5.Modelfile

wait "${ollama_pid}"
