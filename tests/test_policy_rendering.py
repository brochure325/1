"""Behavioral checks for unsolicited discussion and honest chart rendering."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.coaching import diagnostic_signals
from core.policy import ConversationPolicy
from core.presentation import ChartRenderer, chunks, flatten, summary_text


class PolicyTests(unittest.TestCase):
    def test_topic_context_does_not_spread_between_groups(self):
        p=ConversationPolicy()
        self.assertEqual(p.consider("a","战舰世界匹配怎么回事？", "1",now=100),"question")
        p.finish("a",sent=False)
        self.assertIsNone(p.consider("b","队友又不给力", "2",now=101))
        self.assertEqual(p.consider("a","队友又不给力", "3",now=101),"discussion")

    def test_duplicate_and_inflight_messages_are_not_replied_twice(self):
        p=ConversationPolicy()
        p.consider("a","战舰世界怎么打？", "1",now=100)
        self.assertIsNone(p.consider("a","战舰世界怎么打？", "1",now=101))
        self.assertIsNone(p.consider("a","驱逐舰怎么打？", "2",now=101))
        p.finish("a",sent=False)
        self.assertEqual(p.consider("a","驱逐舰怎么打？", "3",now=102),"question")

    def test_commands_not_intercepted_and_context_expires(self):
        p=ConversationPolicy(context_ttl=10)
        self.assertIsNone(p.consider("a","/wows 战绩",now=100))
        p.consider("a","战舰世界真难",now=100)
        p.finish("a",sent=False)
        self.assertIsNone(p.consider("a","队友不给力",now=120))


class PresentationTests(unittest.TestCase):
    def test_collecting_never_draws_invented_recent_numbers(self):
        value=summary_text({"status":"collecting","metrics":None,"message":"等待真实基线"})
        self.assertIn("等待真实基线",value)
        self.assertNotIn("胜率：",value)

    def test_charts_generate_real_png_and_keep_output_bounds(self):
        r=ChartRenderer()
        report={"nickname":"DEMO","region":"asia","mode":"pvp","metrics":{"battles":100,"win_rate":53,"avg_damage":80000}}
        with tempfile.TemporaryDirectory() as directory:
            for name,fn in [("report",lambda p:r.report(report,p,demo=True)),
                            ("rank",lambda p:r.leaderboard({"rows":[report]},p,demo=True)),
                            ("trend",lambda p:r.trend([{"at":1,"totals":{"battles":10,"wins":4}},{"at":2,"totals":{"battles":20,"wins":10}}],p,demo=True))]:
                p=Path(directory)/(name+".png")
                fn(p)
                with Image.open(p) as im:
                    self.assertEqual(im.format,"PNG")
                    self.assertEqual(im.width,1200)
                    self.assertGreater(im.height,200)

    def test_small_sample_coaching_is_explicit(self):
        text="\n".join(diagnostic_signals({"metrics":{"battles":10,"win_rate":40}}))
        self.assertIn("少于30场",text)
        self.assertIn("Wilson",text)
        self.assertIn("没有同船基准和录像",text)

    def test_long_fields_preserve_all_text_between_chunks(self):
        original="甲"*6000
        result=chunks(original,1000)
        self.assertEqual("".join(result),original)
        self.assertTrue(all(len(x)<=1000 for x in result))
        self.assertEqual(flatten({"A":[{"B":1}]}),["A[0].B: 1"])


if __name__=="__main__":
    unittest.main()
