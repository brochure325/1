import json
from pathlib import Path
import tempfile
import unittest

from core.knowledge import KnowledgeBase, import_local


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, entries, reviewed=True):
        (self.root / "corpus.json").write_text(json.dumps({"schema_version": 1, "reviewed": reviewed, "entries": entries}), encoding="utf-8")

    def test_chinese_title_and_alias_retrieval(self):
        self.write([
            {"id": "dd", "title": "驱逐舰控点", "text": "驱逐舰利用隐蔽和视野控制点，不急于开炮。"},
            {"id": "bb", "title": "战列舰交叉火力", "text": "战列舰在外线形成侧射角度。"},
        ])
        kb = KnowledgeBase(self.root)
        self.assertEqual(kb.search("DD怎么控点")[0]["id"], "dd")
        self.assertEqual(kb.search("交叉火力")[0]["id"], "bb")
        self.assertEqual(kb.search(""), [])
        self.assertEqual(kb.search("驱逐", 0), [])

    def test_unreviewed_file_is_not_loaded(self):
        self.write([{"title": "错误结论", "text": "未核实"}], reviewed=False)
        kb = KnowledgeBase(self.root)
        self.assertEqual(kb.entries, [])

    def test_import_fact_requires_source(self):
        source = self.root / "facts.json"
        source.write_text(json.dumps({"schema_version": 1, "entries": [{"title": "冠军", "text": "缺来源", "claim_type": "fact"}]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            import_local(source, self.root / "dest", reviewed=True)

    def test_import_requires_deliberate_review(self):
        source = self.root / "notes.md"
        source.write_text("# 复盘\n\n## 视野\n\n保留退路。", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_local(source, self.root / "dest")
        target = import_local(source, self.root / "dest", reviewed=True)
        self.assertTrue(target.is_file())
        kb = KnowledgeBase(self.root / "dest")
        self.assertEqual(len(kb.entries), 1)
        self.assertEqual(kb.search("视野")[0]["claim_type"], "tactical_inference")

    def test_bad_import_does_not_hide_good_file(self):
        self.write([{"title": "驱逐舰", "text": "控点与视野"}])
        (self.root / "broken.json").write_text("{not json", encoding="utf-8")
        kb = KnowledgeBase(self.root)
        self.assertEqual(len(kb.entries), 1)
        self.assertEqual(len(kb.errors), 1)

    def test_metadata_reaches_prompt(self):
        self.write([{"title": "海王赛", "text": "历史赛制", "claim_type": "fact", "url": "https://example.org/rules", "version": "2023", "updated_at": "2026-10-28", "tags": ["赛事"]}])
        found = KnowledgeBase(self.root).search("海王赛")[0]
        self.assertEqual(found["url"], "https://example.org/rules")
        self.assertEqual(found["version"], "2023")
        self.assertEqual(found["claim_type"], "fact")


if __name__ == "__main__":
    unittest.main()
