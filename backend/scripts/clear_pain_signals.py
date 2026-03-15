import asyncio, os, ssl
import asyncpg

async def main():
    raw = os.environ["DATABASE_URL"]
    dsn = raw.split("?")[0].replace("postgres://", "postgresql://")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = await asyncpg.connect(dsn, ssl=ctx)
    result = await conn.execute("DELETE FROM pain_signals")
    print(f"Cleared: {result}")
    await conn.close()

asyncio.run(main())
