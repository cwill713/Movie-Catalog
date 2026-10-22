import sys
import ollama
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from app.config import get_settings  # noqa: E402

def embed_query(query: str):
    batch = ollama.embed(
      model = 'mxbai-embed-large',
      input = query
      )
    return batch['embeddings'][0]

def get_movie_info_by_title(conn, title):
    COMMAND_BY_TITLE = '''
    SELECT t.imdb_id,
        t.primary_title,
        t.start_year--,
        --t.plot,
        --t.embedding
        FROM titles t
        WHERE t.primary_title like %(title)s'''

    return conn.execute(COMMAND_BY_TITLE, {'title': title}).fetchall()

def get_movie_info_by_query(conn, embedding):
    COMMAND_BY_QUERY = '''
    SELECT t.imdb_id,
        t.primary_title,
        t.start_year,
        t.plot,
        t.embedding <=> %(embedding)s::halfvec AS distance
        FROM titles t
        ORDER BY distance
        LIMIT 30'''

    return conn.execute(COMMAND_BY_QUERY, {'embedding': embedding}).fetchall()

def get_movie_info_by_hnsw(conn, imdb_id):
    COMMAND_BY_HNSW = '''
    SELECT t.imdb_id,
        t.primary_title,
        t.start_year,
        t.plot,
        t.embedding <=> (SELECT embedding FROM titles WHERE imdb_id = %(imdb_id)s) AS distance
        FROM titles t
        WHERE t.imdb_id <> %(imdb_id)s
        ORDER BY distance
        LIMIT 30'''

    return conn.execute(COMMAND_BY_HNSW, {'imdb_id': imdb_id}).fetchall()

def print_titles(rows):
    print(f"{'imdb_id':<11} {'year':<5} title")
    print("-" * 60)
    for imdb_id, title, year in rows:
        print(f"{imdb_id:<11} {year or '?':<5} {title}")
    print(f"\n{len(rows)} row(s)")

def print_neighbours(rows, heading="", plot_width=70):
    if heading:
        print(f"\n=== {heading} ===")
    print(f"{'#':>2}  {'distance':<9} {'imdb_id':<11} {'year':<5} {'title':<35} plot")
    print("-" * 140)
    for rank, (imdb_id, title, year, plot, distance) in enumerate(rows, 1):
        plot = (plot or "")[:plot_width] + ("..." if plot and len(plot) > plot_width else "")
        print(f"{rank:>2}  {distance:<9.4f} {imdb_id:<11} {year or '?':<5} {title[:35]:<35} {plot}")

def main():
      with psycopg.connect(get_settings().require_database_url(), connect_timeout=30) as conn:
        # print_titles(get_movie_info_by_title(conn, 'How to Train%'))

        # print_neighbours(get_movie_info_by_hnsw(conn, 'tt0892769'))

        query = "I want a movie or show about a boy who discovers a dragon, befriends it which goes against societal norms"
        query = "Movie or show about a magical school and a main character who is aoung wizard learnign about his powers for the first time"
        query_embedding = embed_query(query)
        print_neighbours(get_movie_info_by_query(conn, query_embedding),
                         heading=f'Nearest to query: "{query[:60]}..."')


if __name__ == "__main__":
    main()