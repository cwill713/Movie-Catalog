import sys
import ollama
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from app.config import get_settings  # noqa: E402

def embed_query(query: str, use_prefix: bool = True):
    PREFIX = "Represent this sentence for searching relevant passages: "
    text = PREFIX + query if use_prefix else query
    batch = ollama.embed(
      model = 'mxbai-embed-large',
      input = text
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

# Catalog floor from ingest (ADR-012): no title has fewer votes than this.
MIN_VOTES = 2500
DEFAULT_POPULARITY_WEIGHT = 0.03  # 0 = pure similarity, ~0.1 = favour famous titles


def get_movie_info_by_hybrid(conn, embedding, popularity_weight=DEFAULT_POPULARITY_WEIGHT,
                             candidates=300, limit=30):
    """Hybrid search: premise + title similarity, with a small popularity bonus.

    score = 0.5 * d(title vector) + 0.5 * d(premise vector) - popularity_weight * pop
    where pop runs 0..1 on log10(imdb_votes), 0 at MIN_VOTES and 1 at the most-voted
    title (log, so 2.6M votes does not beat 2,500 by a thousand times). Lower is
    better; the last column is this score, not a pure distance.

    popularity_weight guide (tested on 48 queries): 0 for "hidden gem" / "obscure",
    0.03 default, ~0.1 for "classic" / "popular" / "best". Above ~0.15 the results
    are mostly popularity and obscure targets drop out.

    An ORDER BY on a blended score cannot use an HNSW index, so take the top
    `candidates` from each index, then re-rank that small union exactly. Titles
    with no premise fall back to the title vector alone. ef_search is raised
    because the default of 40 missed Breaking Bad entirely.
    """
    COMMAND_BY_HYBRID = '''
    WITH a AS (
        SELECT imdb_id FROM titles
        ORDER BY embedding <=> %(embedding)s::halfvec LIMIT %(candidates)s
    ), b AS (
        SELECT imdb_id FROM titles WHERE premise_embedding IS NOT NULL
        ORDER BY premise_embedding <=> %(embedding)s::halfvec LIMIT %(candidates)s
    ), top AS (
        SELECT max(imdb_votes) AS max_votes FROM titles
    )
    SELECT t.imdb_id,
        t.primary_title,
        t.start_year,
        t.plot,
        0.5 * (t.embedding <=> %(embedding)s::halfvec)
        + 0.5 * COALESCE(t.premise_embedding <=> %(embedding)s::halfvec,
                         t.embedding <=> %(embedding)s::halfvec)
        - %(popularity_weight)s
          * (log(GREATEST(COALESCE(t.imdb_votes, %(min_votes)s), %(min_votes)s))
             - log(%(min_votes)s))
          / (log((SELECT max_votes FROM top)) - log(%(min_votes)s)) AS score
        FROM titles t
        JOIN (SELECT imdb_id FROM a UNION SELECT imdb_id FROM b) c USING (imdb_id)
        ORDER BY score
        LIMIT %(limit)s'''

    conn.execute("SET LOCAL hnsw.ef_search = 400")
    rows = conn.execute(COMMAND_BY_HYBRID,
                        {'embedding': embedding, 'candidates': candidates, 'limit': limit,
                         'popularity_weight': popularity_weight, 'min_votes': MIN_VOTES}).fetchall()
    conn.commit()  # ends the transaction, which also ends the SET LOCAL
    return rows

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

        # query = "I want a movie or show about a boy who discovers a dragon, befriends it which goes against societal norms" 
        # query_embedding = embed_query(query)
        # print_neighbours(get_movie_info_by_query(conn, query_embedding),
        #                  heading=f'Nearest to query: "{query[:60]}..."')

        for use_prefix in [True, False]:
            query = "Movie or show about a magical school and a main character who is aoung wizard learnign about his powers for the first time"
            query_embedding = embed_query(query, use_prefix=use_prefix)
            print_neighbours(get_movie_info_by_query(conn, query_embedding),
                             heading=f'Nearest to query: "{query[:60]}..." (use_prefix={use_prefix})')
        # query = "Movie or show about a magical school and a main character who is aoung wizard learnign about his powers for the first time"
        # query_embedding = embed_query(query)
        # print_neighbours(get_movie_info_by_query(conn, query_embedding),
        #                          heading=f'Nearest to query: "{query[:60]}..."')

        # query = "Moive abouta boys journey through a magic school and his friends as they learn about magic and face challenges"
        # query_embedding = embed_query(query)
        # print_neighbours(get_movie_info_by_query(conn, query_embedding),
        #                         heading=f'Nearest to query: "{query[:60]}..."')       


if __name__ == "__main__":
    main()