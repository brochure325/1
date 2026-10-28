"""Build clearly labelled, artificial chart examples for documentation."""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.presentation import ChartRenderer


def main():
    renderer=ChartRenderer()
    output=ROOT/"docs"/"images"
    report={"nickname":"演示舰长 / DEMO","region":"asia","mode":"pvp","ship_name":"大和 / Yamato",
        "updated_at":1793174400,"metrics":{"battles":120,"win_rate":55.83,"avg_damage":89200,
        "avg_frags":1.1,"avg_xp":1720,"survival_rate":43.33,"main_battery_hit_rate":31.2,
        "avg_spotting":15400,"avg_potential":1425000}}
    renderer.report(report,output/"demo-overview.png",demo=True)
    renderer.leaderboard({"rows":[{"nickname":"DEMO-A","metrics":{"avg_damage":112000}},
        {"nickname":"DEMO-B","metrics":{"avg_damage":96500}},
        {"nickname":"DEMO-C","metrics":{"avg_damage":89200}}]},output/"demo-leaderboard.png",demo=True)
    renderer.trend([{"at":1792569600+i*86400,"totals":{"battles":100+i*5,"wins":52+i*3}} for i in range(7)],output/"demo-trend.png",demo=True)
    print("Three DEMO charts saved in docs/images; no real player data.")


if __name__=="__main__":
    main()
