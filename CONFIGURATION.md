# Sythion (Synth) Configuration Guide

## Quick Start

1. **Create `.env` file** in `sythion/backend/` directory with your API key:
   ```bash
   Sythion (Synth)_OLLAMA_API_KEY=your_actual_api_key_here
   Sythion (Synth)_SELECTED_MODEL=minimax-m3:cloud
   ```

2. **Get your Ollama API Key:**
   - Visit [https://ollama.com](https://ollama.com)
   - Sign up / Log in
   - Go to API settings
   - Generate and copy your API key

3. **Start the backend:**
   ```bash
   cd sythion
   python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
   ```
   or use the one-command launcher: `start_sythion.cmd`

---

## Environment Setup

Sythion (Synth) requires environment variables to be set for production use. Create a `.env` file in the `sythion/backend/` directory or set system environment variables with the `Sythion (Synth)_` prefix.

## Required Configuration

### Ollama Cloud (LLM Provider)

Sythion (Synth) uses Ollama Cloud for LLM inference. You must configure:

```bash
# Ollama Cloud API credentials
Sythion (Synth)_OLLAMA_CLOUD_BASE_URL=https://ollama.com
Sythion (Synth)_OLLAMA_API_KEY=your_api_key_here
Sythion (Synth)_SELECTED_MODEL=minimax-m3:cloud  # Or another supported model
```

**Getting Your API Key:**
1. Visit [Ollama Cloud](https://ollama.com)
2. Create an account or sign in
3. Navigate to API settings
4. Generate a new API key
5. Copy the key to your environment

**Available Models:**
- `minimax-m3:cloud` - Balanced performance (recommended)
- `qwen2.5:7b` - Alternative option
- `llama3.1:8b` - Alternative option

To list available models after setup:
```bash
curl -H "Authorization: Bearer YOUR_API_KEY" https://ollama.com/api/tags
```

### Database Path

```bash
# SQLite database location (Windows example)
Sythion (Synth)_DATABASE_PATH=C:\Users\YourName\AppData\Local\Sythion (Synth)\sythion.db
```

### File Roots (Security)

Define approved directories for file operations:

```bash
# Comma-separated list of allowed directories
Sythion (Synth)_FILE_ROOTS=C:\Users\YourName\Documents,C:\Users\YourName\Downloads
```

### Voice Configuration (Optional)

```bash
# Enable/disable voice features
Sythion (Synth)_VOICE_ENABLED=true

# STT model path (whisper.cpp model)
Sythion (Synth)_STT_MODEL_PATH=C:\models\ggml-base.bin

# TTS voice name (Windows SAPI5)
Sythion (Synth)_TTS_VOICE_NAME=Microsoft Zira Desktop
```

### Security Token

`ControlTokenSecurity` middleware (`backend/core/auth.py`) can validate a
per-launch `X-Control-Token` header, but it is **not enforced on routes yet**,
so the API currently works without a token while bound to 127.0.0.1.

## Quick Test

After configuration, test connectivity:

```bash
cd sythion/backend
python -c "from backend.core.config import settings; print('Config OK:', bool(settings.ollama_api_key))"
```

Or, with the backend running, run the end-to-end smoke test:

```bash
cd sythion
python scripts/smoke_test.py --all
```

## Production Checklist

- [ ] Ollama API key configured
- [ ] Model selected and verified
- [ ] Database path writable
- [ ] File roots defined (security requirement)
- [ ] Voice models downloaded (if using voice)

## Troubleshooting

**"Invalid API key" error:**
- Verify key is copied correctly (no extra spaces)
- Check Ollama Cloud account status
- Ensure network connectivity

**"No models available":**
- Verify API endpoint URL
- List models with the `curl /api/tags` command above

**Database errors:**
- Ensure directory exists and is writable
- Check file permissions on Windows
