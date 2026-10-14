import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import select
from news_agent.db.models import ChannelAnalytics
from news_agent.db.session import init_db, session_scope

async def main():
    await init_db()
    async with session_scope() as s:
        for r in (await s.execute(select(ChannelAnalytics))).scalars():
            d = r.data
            print("FETCHED", r.fetched_at, "period", d["period"])
            for k in ("followers","views_per_post","shares_per_post","reactions_per_post"):
                print("VAL", k, d.get(k))
            print("VAL notifications", d["enabled_notifications"])
            for name, g in d["graphs"].items():
                print("GRAPH", name, g["kind"], "stacked" if g["stacked"] else "", "x:", g["x"][:2], "..", g["x"][-1:], "n=", len(g["x"]))
                for sr in g["series"]:
                    print("   S", sr["key"], sr["name"], sr["values"][:40] if g["kind"]=="pie" else (len(sr["values"]), sr["values"][-5:], "sum", sum(v for v in sr["values"] if isinstance(v,(int,float)))))
asyncio.run(main())
