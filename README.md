# Movie Catalog

A full-stack web app and REST API for managing a personal movie list. Built with **FastAPI**, **SQLite**, and **Jinja2** templates.

---

## Features

- Browse your movie list in a web UI
- Add, edit, and delete movies
- Search movies by title
- Full REST API with automatic Swagger docs
- Lightweight SQLite database (no setup required)

---

## Project Structure

```
app/
├── main.py          # FastAPI app entry point
├── db.py            # Database connection and table initialisation
├── crud.py          # Database query functions
├── schemas.py       # Pydantic request/response models
├── routes/
│   ├── api_movies.py  # REST API endpoints (/api/movies)
│   └── web.py         # Web UI page routes
├── templates/       # Jinja2 HTML templates
└── static/          # CSS and JavaScript
```

---

## Getting Started

### 1. Clone the repo

```bash
git clone <your-repo-url>
cd <repo-folder>
```

### 2. Create and activate a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the app

```bash
uvicorn app.main:app --reload
```

The app starts at **http://127.0.0.1:8000**

---

## Web UI

| Route | Description |
|---|---|
| `GET /` | Home page — lists all movies |
| `GET /movie-input` | Form to add a new movie |
| `GET /movie-edit/{id}` | Form to edit an existing movie |

---

## REST API

Base path: `/api/movies`

Interactive docs available at **http://127.0.0.1:8000/docs**

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/movies` | List all movies |
| `GET` | `/api/movies/search?title=` | Search movies by title |
| `POST` | `/api/movies` | Add a new movie |
| `PUT` | `/api/movies/{id}` | Update a movie |
| `DELETE` | `/api/movies` | Delete one or more movies by ID |

### Movie object

```json
{
  "title": "Inception",
  "year": 2010,
  "genre_one": "Sci-Fi",
  "genre_two": "Thriller",
  "genre_three": null,
  "rating": 8.8
}
```

**Field rules:**
- `title` — 1–100 characters
- `year` — 1888 to current year
- `genre_one` — required, 1–50 characters
- `genre_two` / `genre_three` — optional, up to 50 characters
- `rating` — 0.0 to 10.0

---

## Database

SQLite database (`app/movies.db`) is created automatically on first run. No configuration needed.

---

## Tech Stack

- [FastAPI](https://fastapi.tiangolo.com/)
- [SQLite](https://www.sqlite.org/)
- [Pydantic](https://docs.pydantic.dev/)
- [Jinja2](https://jinja.palletsprojects.com/)
- [Uvicorn](https://www.uvicorn.org/)
