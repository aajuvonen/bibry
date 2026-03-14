# wsgi.py
from app import create_app

# Create the Flask app using the factory function
app = create_app()

# If run as main, start Flask (Gunicorn will use 'app' directly)
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
