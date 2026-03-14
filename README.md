# Bibry

Bibry is a lightweight web interface for browsing and editing a flat BibTeX/BibLaTeX bibliography.
It is designed for personal research libraries where the data lives in a single `.bib` file and an optional directory of PDFs.

The project focuses on simplicity: no database, no accounts, no heavy frameworks.
Everything runs locally through a small Flask backend and a minimal JavaScript frontend.

---

## Features

* Browse a bibliography as a responsive grid of cards or list view
* Search and sort entries (by year, author, or title)
* Instant in-browser editing of BibTeX entries
* Copy entries to the clipboard
* Automatic detection of useful links:
  * DOI links
  * URLs in BibTeX fields
  * `\url{...}` blocks
  * arXiv identifiers (`archiveprefix` + `eprint`) and the like
* Automatic PDF linking when a file named `<bibkey>.pdf` exists
* Mobile-friendly responsive layout

The editor on the right side of the interface always reflects the currently selected entry and allows:

* **SAVE** – update the entry
* **CANCEL** – revert edits
* **ADD** – create a new entry
* **COPY** – copy the raw BibTeX
* **UNDO** – revert last change

Clearing the editor and saving deletes the entry.

---

## Data Layout

Bibry expects a very simple project structure:

```
project/
├── main.bib
├── pdf/
│   ├── Turing1936.pdf
│   └── Planck1901.pdf
└── app/
```

* `main.bib` contains the bibliography
* the `pdf/` directory contains optional PDFs named after the BibTeX key

If a PDF exists with the same key as a BibTeX entry, a **PDF button** appears on the card automatically.

---

## Running the Application

Run:
```
sh run.sh
```

The script provides a simple way to set up the environment and start the Bibry server.

When executed, it performs the following steps:

1. **Create a Python virtual environment**

   ```bash
   python3 -m venv .venv
   ```

   This creates an isolated Python environment in the `.venv/` directory so that project dependencies do not interfere with system Python packages.

2. **Activate the virtual environment**

   ```bash
   . .venv/bin/activate
   ```

   After activation, any Python commands (`python`, `pip`, etc.) run inside the virtual environment.

3. **Install project dependencies**

   ```bash
   pip install -r requirements.txt
   ```

   This installs all Python libraries required by Bibry, such as Flask, Gunicorn, and BibTeX parsing utilities.

4. **Start the web server**

   ```bash
   gunicorn wsgi:app \
     --workers 4 \
     --threads 4 \
     --timeout 120 \
     --bind 0.0.0.0:5000
   ```

   Gunicorn loads the Flask application defined in `wsgi.py` and starts a production-style server.

   The options specify:

   * **`--workers 4`** – run four worker processes to handle requests concurrently
   * **`--threads 4`** – each worker can handle multiple requests using threads
   * **`--timeout 120`** – requests may run for up to 120 seconds before being terminated
   * **`--bind 0.0.0.0:5000`** – listen on port `5000` on all network interfaces

After running the script, Bibry will be available at:

```
http://localhost:5000
```

If the machine is accessible on a local network, the server can also be reached using the host machine’s IP address on port `5000`.


## Running Manually

Create a Python environment and install dependencies:

```
pip install -r requirements.txt
```

Run the development server:

```
python wsgi.py
```

Or run it with Gunicorn:

```
gunicorn wsgi:app
```

Then open:

```
http://localhost:5000
```

---

## Philosophy

Bibry intentionally avoids complex infrastructure.
The goal is to provide a fast and practical interface for working with BibTeX libraries while keeping the entire system transparent and easy to modify.

Your bibliography remains a normal `.bib` file that can be edited, version-controlled, or used with LaTeX as usual.
