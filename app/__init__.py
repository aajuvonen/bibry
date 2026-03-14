# app/__init__.py
from flask import Flask, send_from_directory
from pathlib import Path


ROOT = Path(__file__).parent.parent
PDF_DIR = ROOT / "pdf"


def create_app():

    app = Flask(
        __name__,
        static_folder="static",
        static_url_path=""
    )

    PDF_DIR.mkdir(exist_ok=True)

    from .api import api_bp
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    @app.route("/pdf/<path:filename>")
    def pdf_file(filename):
        return send_from_directory(PDF_DIR, filename)

    return app