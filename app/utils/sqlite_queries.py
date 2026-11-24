"""Helpers for queries with large SQLite IN clauses."""


def iter_bind_batches(values):
    """Batch values below the 999-variable limit, leaving room for other filters."""
    batch_size = 500
    for start in range(0, len(values), batch_size):
        yield values[start:start + batch_size]
