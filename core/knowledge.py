"""Local, source-aware Chinese retrieval. No network requests or model training.

This module has no third-party dependencies. Run ``python -m core.knowledge
--help`` from the plugin directory for the local import utility.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse


CLAIM_TYPES = {"fact", "tactical_inference", "teaching_summary", "replay_observation"}
ALIASES = {
    "dd": "驱逐舰 鱼雷 炮驱 控点", "bb": "战列舰 装甲 交叉火力",
    "ca": "重巡洋舰", "cl": "轻巡洋舰", "cv": "航空母舰 航母 飞机",
    "ss": "潜水艇 潜艇 反潜", "kots": "海王赛 King of the Sea",
    "pr": "个人评分 战绩 样本", "坑": "队友 匹配 吐槽",
    "水表": "战绩 胜率 场均 复盘", "点灯": "视野 隐蔽 驱逐",
    "卖头": "船头 装甲 碾压", "背锅": "复盘 队友 瓶颈",
    "掉血": "血量 资源 承伤", "火烧": "火灾 损管 维修",
}
_META = re.compile(r"<!--\s*wows-knowledge\s+(\{.*?\})\s*-->", re.S)


def _tokens(text: str) -> Counter[str]:
    text = text.casefold()
    tokens = re.findall(r"[a-z0-9_]+", text)
    for word in re.findall(r"[\u3400-\u9fff]+", text):
        tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
        if len(word) == 1:
            tokens.append(word)
    return Counter(tokens)


def _valid_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"https", "http"} and bool(parsed.hostname)


def _normalize(entry: dict[str, Any], origin: str = "") -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("知识条目必须是 JSON 对象")
    title, body = entry.get("title", ""), entry.get("text", "")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("知识条目缺少 title")
    if not isinstance(body, str) or not body.strip():
        raise ValueError(f"知识条目 {title} 缺少 text")
    if len(body) > 30_000:
        raise ValueError(f"知识条目 {title} 过长，请按一个主题拆分为多条")
    claim_type = entry.get("claim_type", "tactical_inference")
    if claim_type not in CLAIM_TYPES:
        raise ValueError(f"不支持的 claim_type: {claim_type}")
    url = entry.get("url", "")
    if not isinstance(url, str) or (url and not _valid_url(url)):
        raise ValueError(f"知识条目 {title} 的 url 必须为网页链接")
    if claim_type in {"fact", "teaching_summary", "replay_observation"} and not url:
        raise ValueError(f"{claim_type} 条目 {title} 必须提供原始来源 url")
    tags = entry.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags):
        raise ValueError(f"知识条目 {title} 的 tags 必须为字符串列表")
    source_ids = entry.get("source_ids", [])
    if not isinstance(source_ids, list) or not all(isinstance(x, str) for x in source_ids):
        raise ValueError(f"知识条目 {title} 的 source_ids 必须为字符串列表")
    uid = entry.get("id") or hashlib.sha256((origin + title + body).encode()).hexdigest()[:16]
    if not isinstance(uid, str) or not uid:
        raise ValueError("id 必须为非空字符串")
    updated = entry.get("updated_at", date.today().isoformat())
    if not isinstance(updated, str):
        raise ValueError("updated_at 必须为日期字符串")
    try:
        date.fromisoformat(updated)
    except ValueError as exc:
        raise ValueError("updated_at 请使用 YYYY-MM-DD") from exc
    return {
        **entry, "id": uid, "title": title.strip(), "text": body.strip(),
        "url": url, "updated_at": updated, "tags": tags, "claim_type": claim_type,
        "version": str(entry.get("version", "未标注版本，具体参数需再核实")),
        "source_ids": source_ids, "origin": origin,
    }


def _markdown_entries(text: str, filename: str) -> list[dict[str, Any]]:
    meta_match = _META.search(text)
    meta = json.loads(meta_match.group(1)) if meta_match else {}
    text = _META.sub("", text)
    sections = re.split(r"(?m)^##\s+", text)
    entries = []
    for section in sections[1:] if len(sections) > 1 else sections:
        title, _, body = section.partition("\n")
        title = title.lstrip("# ").strip()
        if not title or not body.strip():
            continue
        # Keep each normal section intact; split unusually long imported sections
        # at paragraphs so retrieval does not consume the entire context budget.
        chunks: list[str] = []
        current = ""
        for paragraph in body.strip().split("\n\n"):
            if current and len(current) + len(paragraph) > 1800:
                chunks.append(current.strip())
                current = ""
            current += paragraph + "\n\n"
        if current.strip():
            chunks.append(current.strip())
        for n, chunk in enumerate(chunks):
            item = {
                **meta, "title": title if len(chunks) == 1 else f"{title}（{n + 1}）",
                "text": chunk, "id": hashlib.sha256((filename + title + str(n)).encode()).hexdigest()[:16],
            }
            entries.append(_normalize(item, filename))
    return entries


class KnowledgeBase:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.entries: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._sources: list[dict[str, Any]] = []
        self.reload()

    def reload(self) -> int:
        self.entries, self._sources, self.errors = [], [], []
        self.root.mkdir(parents=True, exist_ok=True)
        seen: set[str] = set()
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".md", ".json"}:
                continue
            try:
                content = path.read_text(encoding="utf-8-sig")
                if path.suffix.lower() == ".md":
                    # Markdown must explicitly carry reviewed metadata. Drafts and
                    # arbitrary files in this folder never become evidence silently.
                    match = _META.search(content)
                    if not match or json.loads(match.group(1)).get("reviewed") is not True:
                        continue
                    items = _markdown_entries(content, path.name)
                else:
                    data = json.loads(content)
                    if not isinstance(data, dict) or data.get("schema_version") != 1:
                        continue
                    if data.get("reviewed") is not True:
                        continue
                    sources = data.get("sources", [])
                    if not isinstance(sources, list) or not all(isinstance(s, dict) for s in sources):
                        raise ValueError("sources 必须为对象列表")
                    self._sources.extend(sources)
                    items = [_normalize(e, path.name) for e in data.get("entries", [])]
                for entry in items:
                    if entry["id"] not in seen:
                        self.entries.append(entry)
                        seen.add(entry["id"])
            except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
                self.errors.append(f"{path.name}: {exc}")
        self._index = [_tokens(" ".join([e["title"]] * 3 + e["tags"] * 2 + [e["text"]])) for e in self.entries]
        self._df = Counter(token for doc in self._index for token in doc)
        self._avg_len = sum(sum(d.values()) for d in self._index) / max(len(self._index), 1)
        return len(self.entries)

    def sources(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self._sources]

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not query.strip() or limit <= 0:
            return []
        query = query[:2000].casefold()
        expanded = query
        for alias, meaning in ALIASES.items():
            if (alias.isascii() and re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", query)) or (not alias.isascii() and alias in query):
                expanded += " " + meaning
        words = _tokens(expanded)
        scores = []
        count = len(self.entries)
        for entry, doc in zip(self.entries, self._index):
            length = sum(doc.values())
            score = 0.0
            for token in words:
                tf = doc.get(token, 0)
                if not tf:
                    continue
                idf = math.log(1 + (count - self._df[token] + .5) / (self._df[token] + .5))
                score += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * length / max(self._avg_len, 1)))
            if query in entry["title"].casefold():
                score += 8
            if score > 0:
                scores.append((score, entry))
        scores.sort(key=lambda pair: pair[0], reverse=True)
        return [{**entry, "score": round(score, 4)} for score, entry in scores[:min(limit, 20)]]


def import_local(path: str | Path, output_dir: str | Path, *, reviewed: bool = False) -> Path:
    """Validate and copy a deliberately reviewed local source into the corpus.

    Source text is data: nothing is evaluated, executed, or fetched by this tool.
    """
    source = Path(path)
    if not source.is_file():
        raise ValueError("请输入存在的本地文件路径，不支持网址")
    if source.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("文件超过 10 MB，请拆分后导入")
    if not reviewed:
        raise ValueError("请先核对授权、来源与版本，再显式使用 reviewed=True / --reviewed")
    text = source.read_text(encoding="utf-8-sig")
    if source.suffix.lower() == ".json":
        payload = json.loads(text)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("JSON 顶层需要 schema_version: 1 和 entries 列表")
        entries = payload.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError("entries 必须为非空列表")
        entries = [_normalize(entry, source.name) for entry in entries]
        sources = payload.get("sources", [])
        if not isinstance(sources, list) or not all(isinstance(s, dict) for s in sources):
            raise ValueError("sources 必须为对象列表")
    elif source.suffix.lower() == ".md":
        entries = _markdown_entries(text, source.name)
        if not entries:
            raise ValueError("Markdown 需要标题和正文")
        sources = []
    else:
        raise ValueError("仅支持 .json 和 .md 文件；录播请先整理成授权文字摘要")
    result = {"schema_version": 1, "reviewed": True, "sources": sources, "entries": entries}
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    target = target_dir / ("imported_" + digest + ".json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="战舰世界本地知识库导入和检索（不下载视频、不训练模型）")
    parser.add_argument("--root", default="knowledge", help="知识库目录")
    parser.add_argument("--import-file", help="要导入的本地 .json 或 .md 文件")
    parser.add_argument("--reviewed", action="store_true", help="确认已核对来源、版本与使用授权")
    parser.add_argument("--search", help="测试检索问题")
    args = parser.parse_args()
    if args.import_file:
        print(import_local(args.import_file, args.root, reviewed=args.reviewed))
    kb = KnowledgeBase(args.root)
    if args.search:
        print(json.dumps(kb.search(args.search), ensure_ascii=False, indent=2))
    else:
        print(f"已载入 {len(kb.entries)} 条；异常 {len(kb.errors)} 个")
    for error in kb.errors:
        print(error)


if __name__ == "__main__":
    main()
