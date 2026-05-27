from app.db import get_connection
from app.schemas import MovieCreate


def create_movie(movie: MovieCreate) -> dict:
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO movies (title, year, genre_one, genre_two, genre_three, rating) VALUES (?, ?, ?, ?, ?, ?)",
        (movie.title, movie.year, movie.genre_one, movie.genre_two, movie.genre_three, movie.rating),
    )
    movie_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return {"id": movie_id, **movie.dict()}

def get_movies() -> list[dict]:
    connection = get_connection()
    rows = connection.execute("SELECT id, title, year, genre_one, genre_two, genre_three, rating FROM movies ORDER BY id DESC").fetchall()
    connection.close()
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "year": row["year"],
            "genre_one": row["genre_one"],
            "genre_two": row["genre_two"],
            "genre_three": row["genre_three"],
            "rating": row["rating"],
        }
        for row in rows
    ]

def get_movie(movie_id: int) -> dict | None:
    connection = get_connection()
    row = connection.execute(
        "SELECT id, title, year, genre_one, genre_two, genre_three, rating FROM movies WHERE id = ?",
        (movie_id,),
    ).fetchone()
    connection.close()
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "year": row["year"],
        "genre_one": row["genre_one"],
        "genre_two": row["genre_two"],
        "genre_three": row["genre_three"],
        "rating": row["rating"],
    }

def search_movies(movie_title: str) -> list[dict] | None:
    connection = get_connection()
    rows = connection.execute("SELECT id, title, year, genre_one, genre_two, genre_three, rating FROM movies WHERE title LIKE ?", (f"%{movie_title}%",)).fetchall()
    connection.close()
    if not rows:
        return None
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "year": row["year"],
            "genre_one": row["genre_one"],
            "genre_two": row["genre_two"],
            "genre_three": row["genre_three"],
            "rating": row["rating"],
        }
        for row in rows
    ]

def delete_movies(ids: list[int]) -> int:
    connection = get_connection()
    placeholders = ",".join("?" * len(ids))
    cursor = connection.execute(f"DELETE FROM movies WHERE id IN ({placeholders})", ids)
    deleted = cursor.rowcount
    connection.commit()
    connection.close()
    return deleted

def update_movie(movie_id: int, movie: MovieCreate) -> dict | None:
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE movies SET title=?, year=?, genre_one=?, genre_two=?, genre_three=?, rating=? WHERE id=?",
        (movie.title, movie.year, movie.genre_one, movie.genre_two, movie.genre_three, movie.rating, movie_id),
    )
    connection.commit()
    if cursor.rowcount == 0:
        connection.close()
        return None
    connection.close()
    return {"id": movie_id, **movie.dict()}