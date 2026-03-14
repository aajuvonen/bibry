# run.sh
#!/bin/bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# Start Gunicorn with our WSGI app
gunicorn wsgi:app \
  --workers 4 \
  --threads 4 \
  --timeout 120 \
  --bind 0.0.0.0:5000
