"""
Additive schema migration.

Run after deploying a change that adds columns:

    venv/bin/python update_db.py

This only ever ADDS things. The previous version of this script called
db.drop_all(), which destroyed every user account and conversion record.
"""
from sqlalchemy import inspect, text

from app import app
from models import db

# (table, column, DDL type) — added only when missing.
NEW_COLUMNS = [
    ('task_progress', 'user_id', 'INTEGER NULL'),
    ('task_progress', 'original_filename', 'VARCHAR(255) NULL'),
    ('task_progress', 'row_count', 'INTEGER NULL'),
    ('task_progress', 'flagged_count', 'INTEGER NULL'),
]


def migrate():
    with app.app_context():
        # Creates any table that does not exist yet; never touches existing ones.
        db.create_all()

        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

        added = 0
        for table, column, ddl_type in NEW_COLUMNS:
            if table not in existing_tables:
                print(f"[SKIP] Table '{table}' does not exist yet.")
                continue

            columns = {c['name'] for c in inspector.get_columns(table)}
            if column in columns:
                print(f"[OK]   {table}.{column} already present.")
                continue

            with db.engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl_type}'))
            print(f"[ADD]  {table}.{column}")
            added += 1

        print(f"\n[DONE] {added} column(s) added. No data was dropped.")


if __name__ == '__main__':
    migrate()
