"""Read-only local installation diagnosis; prints no credential values."""
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    print("WoWS Captain installation checks")
    failed=False
    print("Python:",sys.version.split()[0])
    if sys.version_info < (3,12):
        print("ERROR: Python 3.12+ required")
        failed=True
    for name in ("aiohttp","PIL"):
        exists=importlib.util.find_spec(name) is not None
        print(name, "OK" if exists else "MISSING: install requirements.txt")
        failed=failed or not exists
    schema=json.loads((ROOT/"_conf_schema.json").read_text("utf-8"))
    print("Configuration schema:",len(schema),"fields")
    from core.knowledge import KnowledgeBase
    kb=KnowledgeBase(ROOT/"knowledge")
    print("Knowledge:",len(kb.entries),"entries;",len(kb.sources()),"sources")
    if not kb.entries or kb.errors:
        failed=True
        print("ERROR: missing or invalid knowledge entries")
        for error in kb.errors:
            print(error)
    for variable in ("WOWS_WG_APPLICATION_ID","WOWS_HAOJIAN_COOKIE","WOWS_KOKOMI_BRIDGE_TOKEN"):
        print(variable,"SET" if os.getenv(variable) else "not set (may be optional or configured elsewhere)")
    if not failed:
        from core.presentation import ChartRenderer
        print("Chinese chart font:","found" if ChartRenderer().zh else "not found; charts use English labels")
    print("Local checks only. No claim of authenticated WG, Haojian or Kokomi connectivity.")
    return 1 if failed else 0


if __name__=="__main__":
    raise SystemExit(main())
