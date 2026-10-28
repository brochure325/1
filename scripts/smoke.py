"""Public Haojian live smoke; WG runs only with explicit personal credentials."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.haojian import HaojianClient
from core.wg import WargamingClient


async def main():
    h=HaojianClient()
    try:
        status=await h.status()
        ships=await h.search("大和")
        ship=next(s for s in ships if s["index"]=="PJSB018")
        detail=await h.detail(ship["index"])
        print(json.dumps({"Haojian":"PASS","version":detail["version"],"ship":ship["name"],
            "sections":len(detail["comparison"]),"raw_fields":list(detail["raw"]),
            "authenticated":status.get("authenticated"),"cookie_configured":status.get("cookie_configured")},ensure_ascii=False))
    finally:
        await h.close()
    app_id=os.getenv("WOWS_WG_APPLICATION_ID","")
    player=os.getenv("WOWS_TEST_PLAYER","")
    if app_id and player:
        client=WargamingClient(app_id)
        try:
            rows=await client.search_players(os.getenv("WOWS_TEST_REGION","asia"),player)
            print("WG search:",len(rows),"result(s)")
        finally:
            await client.close()
    else:
        print("WG live test SKIPPED: set own WOWS_WG_APPLICATION_ID and WOWS_TEST_PLAYER to run.")
    print("Kokomi live test requires a running authorized V4 engine; not covered by this public smoke test.")


if __name__=="__main__":
    asyncio.run(main())
