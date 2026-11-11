"""Ablation: which parts of the embedded text help or hurt retrieval?

Read-only against the database (no writes, no scratch tables). It takes a
sample of the catalog, re-embeds each text *variant* locally with Ollama, and
ranks every sample title against each test query in plain Python. The "full" variant
reuses the vectors already stored in titles.embedding, so it is the control.

Ranks are within the sample, not the full 37,605, so compare variants to each
other, not to the ranks you saw in the real catalog.

Run:  python scripts/embedding_ablation.py --variants full
      python scripts/embedding_ablation.py            (all variants)
"""
import argparse
import json
import sys
from pathlib import Path

import math
import ollama
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402
from db_embedding import embed_db_rows  # noqa: E402  (same embed call as the real pipeline)
from testing_embeddings import embed_query  # noqa: E402  (adds the query prefix)

CACHE_DIR = Path(__file__).resolve().parent / ".ablation_cache"  # local only; delete freely

VARIANTS = ["full", "no_title", "plot_only"]

# (label, imdb_id of the title that SHOULD come back, query). Title words are
# left out of the queries. Add more rows here.
QUERIES = [
    # Breaking Bad
    ("bb plot", "tt0903747", "A high school chemistry teacher is diagnosed with terminal lung cancer "
                             "and starts making drugs to leave money for his family"),
    ("bb vague", "tt0903747", "an ordinary teacher turns into a criminal after a bad diagnosis"),
    ("bb keywords", "tt0903747", "chemistry teacher, cancer, meth, Albuquerque, partner"),
    # Harry Potter and the Sorcerer's Stone
    ("hp typos", "tt0241527", "Movie or show about a magical school and a main character who is aoung "
                              "wizard learnign about his powers for the first time"),
    ("hp vibe", "tt0241527", "cozy magical boarding school adventure about a chosen-one boy"),
    ("hp keywords", "tt0241527", "lightning scar, houses, broomsticks"),
    ("hp short", "tt0241527", "magic school"),
    # How to Train Your Dragon
    ("httyd plot", "tt0892769", "I want a movie or show about a boy who discovers a dragon, befriends it "
                               "which goes against societal norms"),
    ("httyd vague", "tt0892769", "a young viking and a creature everyone else wants to kill"),
    # Known answers from session 9
    ("jaws", "tt0073195", "A giant shark terrorizes a beach town at the start of summer and three men "
                          "go out on a boat to hunt it"),
    ("inception", "tt1375666", "A thief who steals secrets by entering people's dreams is given the "
                               "task of planting an idea in someone's mind"),
    ("matrix", "tt0133093", "A hacker learns his reality is a simulation and joins a rebellion "
                            "against the machines"),
]
TARGETS = sorted({t for _, t, _ in QUERIES})


def make_variant(embedding_text: str, variant: str) -> str:
    """Return the text to embed for this variant.

    embedding_text is the stored string, one field per line:
        Breaking Bad (2008) - tvSeries
        Genres: Crime, Drama, Thriller
        Director: ...
        Actors: ...
        Plot: ...
        Content Rating: TV-MA
        IMDb ID: tt0903747

    "full" must return it unchanged (the harness doesn't call this for "full",
    it reuses stored vectors). Decide what "no_title" and "plot_only" mean.
    """
    lines = embedding_text.split("\n")  # line 0 is the heading: title, year, type
    if variant == "full":
        return embedding_text
    if variant == "no_title":
        # Drop only the heading. Everything else (incl. cast and IMDb ID) stays,
        # so the only thing that changes is the title.
        return "\n".join(lines[1:])
    if variant == "plot_only":
        # Keep only the plot line: no title, genres, people, rating or ID.
        plot = [ln for ln in lines if ln.startswith("Plot: ")]
        return "\n".join(plot)
    raise ValueError(f"unknown variant: {variant}")


def load_sample(conn, size):
    """Top `size` titles by votes, plus every target, as {imdb_id: (title, text, vector)}."""
    rows = conn.execute(
        """
        (SELECT imdb_id, primary_title, embedding_text, embedding::text
           FROM titles WHERE embedding IS NOT NULL
          ORDER BY imdb_votes DESC NULLS LAST LIMIT %(n)s)
        UNION
        (SELECT imdb_id, primary_title, embedding_text, embedding::text
           FROM titles WHERE imdb_id = ANY(%(targets)s::text[]))
        """,
        {"n": size, "targets": TARGETS},
    ).fetchall()
    return {r[0]: (r[1], r[2], json.loads(r[3])) for r in rows}


def variant_matrix(variant, ids, sample):
    """One vector per id for a variant, embedding locally and caching to disk."""
    if variant == "full":
        return [sample[i][2] for i in ids]
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{variant}.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    todo = [i for i in ids if i not in cache]
    for n, i in enumerate(todo, 1):
        cache[i] = embed_db_rows(make_variant(sample[i][1], variant))
        if n % 100 == 0:
            print(f"  {variant}: embedded {n}/{len(todo)}", flush=True)
            path.write_text(json.dumps(cache))
    if todo:
        path.write_text(json.dumps(cache))
    return [cache[i] for i in ids]


REWRITE_PROMPT = (
    "Rewrite the user's request as a short plot synopsis (1-2 sentences) of the kind "
    "of movie or show they describe, written like a database plot summary. Fix any "
    "typos. Describe the story; do not list keywords or mention that it is a request. "
    "Output only the synopsis."
)


def rewrite_query(query: str) -> str:
    """Turn a raw search request into a plot-style sentence (local llama3.1)."""
    resp = ollama.chat(
        model="llama3.1",
        messages=[{"role": "system", "content": REWRITE_PROMPT},
                  {"role": "user", "content": query}],
        options={"temperature": 0},
    )
    return resp["message"]["content"].strip()


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def rank_of(target, ids, matrix, query_vec):
    """1-based rank of target by cosine distance (same metric as <=>)."""
    q = _unit(query_vec)
    dist = {i: 1 - sum(a * b for a, b in zip(_unit(v), q)) for i, v in zip(ids, matrix)}
    return sorted(dist, key=dist.get).index(target) + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", default=VARIANTS, choices=VARIANTS)
    ap.add_argument("--rewrite", action="store_true", help="also compare raw vs rewritten queries")
    ap.add_argument("--sample", type=int, default=2000)
    args = ap.parse_args()

    with psycopg.connect(get_settings().require_database_url(), connect_timeout=30) as conn:
        sample = load_sample(conn, args.sample)
    ids = sorted(sample)
    print(f"sample: {len(ids)} titles, {len(QUERIES)} queries\n")

    query_vecs = {label: embed_query(q) for label, _, q in QUERIES}

    results = {}  # variant -> label -> rank
    for variant in args.variants:
        matrix = variant_matrix(variant, ids, sample)
        results[variant] = {label: rank_of(t, ids, matrix, query_vecs[label]) for label, t, _ in QUERIES}

    width = max(len(v) for v in args.variants) + 2
    print(f"{'query':<16}{'target':<28}" + "".join(f"{v:>{width}}" for v in args.variants))
    for label, t, _ in QUERIES:
        print(f"{label:<16}{sample[t][0][:26]:<28}"
              + "".join(f"{results[v][label]:>{width}}" for v in args.variants))

    base = args.variants[0]
    for v in args.variants[1:]:
        better = sum(results[v][l] < results[base][l] for l, _, _ in QUERIES)
        worse = sum(results[v][l] > results[base][l] for l, _, _ in QUERIES)
        print(f"\n{v} vs {base}: better on {better}, worse on {worse}, tied on {len(QUERIES) - better - worse}")

    if args.rewrite:
        base = args.variants[0]
        matrix = variant_matrix(base, ids, sample)
        print(f"\nQuery rewrite, on the '{base}' vectors:")
        print(f"{'query':<16}{'raw':>6}{'rewritten':>11}")
        better = worse = 0
        for label, t, q in QUERIES:
            rewritten = rewrite_query(q)
            r = rank_of(t, ids, matrix, embed_query(rewritten))
            raw = results[base][label]
            better += r < raw
            worse += r > raw
            print(f"{label:<16}{raw:>6}{r:>11}   {rewritten}")
        print(f"\nrewritten vs raw: better on {better}, worse on {worse}, tied on {len(QUERIES) - better - worse}")


if __name__ == "__main__":
    main()
