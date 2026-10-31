"""Normalize complete Crossref dates; retain partial dates as unknown, not invented days."""
import datetime

def normalize(row):
    value = row.get('published_at')
    if row.get('source') != 'crossref' or not value:
        return row
    row = dict(row)
    row['published_at_raw'] = value
    parts = value.split('-')
    try:
        row['published_at'] = datetime.date(*map(int, parts)).isoformat() if len(parts) == 3 else None
    except ValueError:
        row['published_at'] = None
    return row
