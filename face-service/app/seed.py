"""Standalone seeder: `python -m app.seed [--force]` to (re)load the sample roster."""
import sys
from .db import get_store


def main():
    force = "--force" in sys.argv
    store = get_store()
    store.ping()
    store.seed(force=force)
    print(f"Seeded roster ({'reset' if force else 'upsert'}). "
          f"{store.count()} students, {store.enrolled_count()} enrolled.")


if __name__ == "__main__":
    main()
