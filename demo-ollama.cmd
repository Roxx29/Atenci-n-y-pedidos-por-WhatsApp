@echo off
rem Ollama solo para la demo: modelos en F:\Modelo de IA, puerto 11435.
rem No toca el Ollama de MoneyPrinter (E:\MoneyPrinter-modelos, puerto 11434).
rem El bot usa: ANTHROPIC_BASE_URL=http://127.0.0.1:11435  LLM_MODEL=qwen3:8b
set "OLLAMA_MODELS=F:\Modelo de IA"
set "OLLAMA_HOST=127.0.0.1:11435"
"E:\MoneyPrinter-programas\Ollama\ollama.exe" serve
