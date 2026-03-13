"""Initialize the database schema."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.db.database import init_db


async def main():
    print("Initializing database...")
    await init_db()
    print("✓ Database initialized successfully")


if __name__ == "__main__":
    asyncio.run(main())
