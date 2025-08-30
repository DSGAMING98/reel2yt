# Reel → YouTube Match (Streamlit)

Find the **same** video on YouTube from an Instagram Reel/TV/Post link. It verifies with **frame pHash + optional audio fingerprint**, not just text search.

## Quickstart
```bash
# 1) cd into project root (where requirements.txt lives)
python -m venv .venv && . .venv/Scripts/activate  # Windows
# or: python3 -m venv .venv && source .venv/bin/activate  # macOS/Linux

pip install -r requirements.txt

# 2) (optional) copy env
cp .env.example .env  # Windows: copy .env.example .env
# put your YT_API_KEY in .env if you want official API. Otherwise it auto-falls back.

# 3) make sure ffmpeg exists (see below), then run:
streamlit run app/streamlit_app.py
