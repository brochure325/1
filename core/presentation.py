"""Text output and portable Pillow charts, no browser renderer required."""
from __future__ import annotations

import math
import os
import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

METRICS = [("battles", "场次", "Battles", 0), ("win_rate", "胜率", "Win rate %", 2),
           ("avg_damage", "场均伤害", "Avg damage", 0), ("avg_frags", "场均击杀", "Avg frags", 2),
           ("avg_xp", "场均经验", "Avg XP", 0), ("survival_rate", "存活率", "Survival %", 2),
           ("main_battery_hit_rate", "主炮命中率", "Main hit %", 2),
           ("avg_spotting", "场均侦察伤害", "Avg spotting", 0), ("avg_potential", "场均潜在伤害", "Avg potential", 0)]


def number(value, digits=0):
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    return f"{value:,.{digits}f}"


def timestamp(value):
    if isinstance(value,(int,float)):
        return datetime.fromtimestamp(value,timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M +08:00")
    return str(value or "")


def summary_text(report: dict) -> str:
    heading = report.get("nickname") or report.get("title") or str(report.get("account_id", "WoWS"))
    lines = [f"{heading} | {report.get('region', '')} | {report.get('mode', 'pvp')}"]
    if report.get("status") == "collecting":
        return "\n".join(lines + [report.get("message") or report.get("note") or "已保存当前累计快照。尚无该期间之前的基线，不能计算安装前的近期战绩；请打完若干场后再次查询。"])
    if report.get("ship_name"):
        lines.append(f"舰船：{report['ship_name']}")
    if report.get("period_start"):
        lines.append(f"实际区间：{timestamp(report['period_start'])} → {timestamp(report.get('period_end'))}")
        if report.get("period_note"):
            lines.append(report["period_note"])
    for key, label, _, digits in METRICS:
        if key in report.get("metrics", {}):
            value=report['metrics'][key]
            suffix="%" if key.endswith("_rate") and value is not None else ""
            lines.append(f"{label}：{number(value, digits)}{suffix}")
    if report.get("updated_at"):
        lines.append(f"数据时间：{timestamp(report['updated_at'])}")
    lines.append("PR：" + str(report.get("metrics", {}).get("pr_note", "未配置可信单船期待值，不生成PR")))
    if report.get("metrics", {}).get("xp_note"):
        lines.append(report["metrics"]["xp_note"])
    return "\n".join(lines)


def flatten(data, prefix="", *, max_lines=160) -> list[str]:
    """Preserve field paths and values without claiming arbitrary units."""
    result = []
    def visit(node, path):
        if len(result) >= max_lines:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                visit(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, f"{path}[{index}]")
        else:
            result.append(f"{path}: {node}")
    visit(data, prefix)
    return result


def chunks(text: str, limit=2800) -> list[str]:
    lines, current, length = [], [], 0
    for line in text.splitlines():
        for part in textwrap.wrap(line, width=limit, replace_whitespace=False, drop_whitespace=False) or [""]:
            if current and length + len(part) + 1 > limit:
                lines.append("\n".join(current))
                current, length = [], 0
            current.append(part)
            length += len(part) + 1
    if current:
        lines.append("\n".join(current))
    return lines


class ChartRenderer:
    def __init__(self, font_path=""):
        candidates = [font_path, os.getenv("WOWS_FONT_PATH", ""), "C:/Windows/Fonts/msyh.ttc",
                      "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                      "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                      "/System/Library/Fonts/PingFang.ttc"]
        self.font_path = next((p for p in candidates if p and Path(p).is_file()), "")
        self.zh = bool(self.font_path)

    def font(self, size):
        if self.font_path:
            return ImageFont.truetype(self.font_path, size)
        return ImageFont.load_default(size=size)

    def report(self, report: dict, path: str | Path, *, demo=False) -> str:
        im = Image.new("RGB", (1200, 870), "#101a2c")
        d = ImageDraw.Draw(im)
        def text(pos, s, size=24, color="#e9f0fb"):
            # Pillow draws arbitrary nicknames as text, never interprets markup.
            d.text(pos, str(s)[:70], font=self.font(size), fill=color)
        text((50, 30), "WoWS Captain", 40)
        text((50, 88), report.get("nickname") or report.get("title") or "Player", 30)
        text((50, 134), f"{report.get('region','')}  /  {report.get('mode','pvp')}  /  {report.get('ship_name','Overall')}", 22, "#a9bad4")
        m = report.get("metrics", {})
        for idx, (key, zh, en, digits) in enumerate(METRICS):
            x, y = 50 + (idx % 3)*370, 190 + (idx // 3)*145
            d.rounded_rectangle((x,y,x+350,y+125), radius=14, fill="#1c2b43")
            text((x+20,y+15), zh if self.zh else en, 22, "#a9bad4")
            suffix="%" if key.endswith("_rate") and m.get(key) is not None else ""
            text((x+20,y+54), number(m.get(key), digits)+suffix, 37, "#55d7b3")
        rate = m.get("win_rate")
        if rate is not None:
            d.rounded_rectangle((50, 645, 1150, 669), radius=10, fill="#d9757a")
            d.rounded_rectangle((50, 645, 50+max(1, min(100,rate))*11, 669), radius=10, fill="#55d7b3")
        period = f"{timestamp(report.get('period_start'))} → {timestamp(report.get('period_end'))}" if report.get("period_start") else timestamp(report.get("updated_at"))
        text((50,700), period, 19, "#a9bad4")
        text((50,739), "官方累计数据 / 近期为快照差值；PR缺期待值不计算" if self.zh else "WG totals / recent = snapshot delta / no invented PR", 20, "#a9bad4")
        text((50,785), "示例数据 · 仅用于演示，非真实玩家" if demo and self.zh else "DEMO DATA - NOT A REAL PLAYER" if demo else "Source: Wargaming Public API", 22, "#ffc36a" if demo else "#7894bb")
        return self._save(im, path)

    def leaderboard(self, data: dict, path: str | Path, *, demo=False) -> str:
        rows = data.get("rows", [])[:20]
        height = 205 + max(1, len(rows))*69
        im = Image.new("RGB", (1200, height), "#101a2c")
        d = ImageDraw.Draw(im)
        d.text((45,24), "WoWS Captain | " + ("单船候选池排行榜" if self.zh else "Ship Candidate Leaderboard"), font=self.font(31), fill="#e9f0fb")
        d.text((45,75), ("候选玩家池，不代表全服排名" if self.zh else "Candidate pool; NOT a server-wide ranking"),font=self.font(23),fill="#ffc36a")
        sort_by = data.get("sort_by", "avg_damage")
        values = [float((r.get("metrics") or {}).get(sort_by) or 0) for r in rows]
        maximum = max(values or [1]) or 1
        for i, (row, value) in enumerate(zip(rows,values)):
            y = 125+i*69
            d.text((45,y), f"{i+1:02}  {str(row.get('nickname', row.get('account_id','')))[:22]}", font=self.font(22), fill="#e9f0fb")
            d.rounded_rectangle((420,y,420+max(2,value/maximum*500),y+30),radius=6,fill="#55d7b3")
            d.text((945,y), number(value,2 if sort_by=="win_rate" else 0),font=self.font(23),fill="#e9f0fb")
        d.text((45,height-60), ("演示数据" if self.zh else "DEMO DATA") if demo else f"Sort: {sort_by} | Min battles: {data.get('min_battles',20)}", font=self.font(20),fill="#a9bad4")
        return self._save(im,path)

    def trend(self, points: list[dict], path: str | Path, *, demo=False) -> str:
        im = Image.new("RGB",(1200,660),"#101a2c")
        d = ImageDraw.Draw(im)
        d.text((45,25), "WoWS Captain | " + ("累计胜率快照趋势" if self.zh else "Cumulative Win-rate Snapshots"),font=self.font(32),fill="#e9f0fb")
        d.text((45,78), "累计口径，非逐场结果" if self.zh else "Cumulative snapshots; NOT individual match results",font=self.font(23),fill="#ffc36a")
        for pct in [0,25,50,75,100]:
            y=535-pct*3.8
            d.line((110,y,1130,y),fill="#293950",width=1)
            d.text((40,y-12),f"{pct}%",font=self.font(20),fill="#a9bad4")
        coords=[]
        times=[float(p.get("at",0)) for p in points]
        first=min(times) if times else 0
        duration=max(times)-first if times else 0
        for idx,p in enumerate(points):
            totals = p.get("totals",p.get("raw",{}))
            n=totals.get("battles",0)
            if n:
                offset=(times[idx]-first)/duration if duration>0 else 0
                coords.append((110+offset*1020,535-totals.get("wins",0)/n*380))
        if len(coords)>1:
            d.line(coords,fill="#55d7b3",width=5)
        for x,y in coords:
            d.ellipse((x-5,y-5,x+5,y+5),fill="#55d7b3")
        d.text((110,560), timestamp(points[0].get("at"))[:16] if points else "No snapshots",font=self.font(19),fill="#a9bad4")
        d.text((760,560),timestamp(points[-1].get("at"))[:16] if points else "",font=self.font(19),fill="#a9bad4")
        d.text((45,610), "DEMO DATA" if demo else "Source: local snapshots of Wargaming cumulative statistics",font=self.font(20),fill="#a9bad4")
        return self._save(im,path)

    @staticmethod
    def _save(im, path):
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        im.save(path,"PNG")
        return str(path.resolve())
