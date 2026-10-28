"""Topic participation policy. Only platform-delivered messages are considered."""
from __future__ import annotations

import re
import time
from collections import OrderedDict, deque

STRONG = re.compile(
    r"战舰世界|戰艦世界|\bwows\b|world\s+of\s+warships|窝窝屎|窝窝|浩舰|海王赛|"
    r"战列舰|巡洋舰|驱逐舰|航母|潜艇|水听|雷达船|公会战|穿甲弹|高爆弹|"
    r"大和|岛风|得梅因|兴登堡|蒙大拿|彼得罗巴甫洛夫斯克|克里姆林|施里芬",
    re.IGNORECASE,
)
WEAK = re.compile(
    r"匹配|队友|胜率|场均|水表|鱼雷|雷达|隐蔽|跳弹|过穿|控点|点亮|侧翼|黑枪|连败|版本|"
    r"(?<![A-Za-z0-9_])(?:HE|AP|PR)(?![A-Za-z0-9_])", re.I,
)
QUESTION = re.compile(r"[?？]|怎么|为什么|如何|多少|能不能|是不是|有没有|哪艘|什么|求教|请问|咋|建议|推荐|配装|加点")
COMMAND = re.compile(r"^/?(?:wows|窝窝|水表)(?:\s|$)", re.I)


class ConversationPolicy:
    def __init__(self, cooldown: float = 120, context_ttl: float = 600, max_sessions: int = 300):
        self.cooldown = max(0, cooldown)
        self.context_ttl = max(0, context_ttl)
        self.max_sessions = max_sessions
        self.sessions: OrderedDict[str, dict] = OrderedDict()

    def _state(self, key: str, now: float) -> dict:
        state = self.sessions.pop(key, None)
        if state is None or now - state["last_topic"] > self.context_ttl:
            state = {"last_topic": 0.0, "last_reply": -1e12, "busy": False,
                     "messages": deque(maxlen=10), "seen": deque(maxlen=100)}
        self.sessions[key] = state
        while len(self.sessions) > self.max_sessions:
            self.sessions.popitem(last=False)
        return state

    def consider(self, key: str, text: str, message_id: str = "", *, now: float | None = None) -> str | None:
        """Return question/discussion, reserving the session until finish()."""
        now = time.monotonic() if now is None else now
        if not text.strip() or COMMAND.match(text.strip()):
            return None
        state = self._state(key, now)
        if message_id and message_id in state["seen"]:
            return None
        if message_id:
            state["seen"].append(message_id)
        strong = bool(STRONG.search(text))
        related = strong or (state["last_topic"] > 0 and now - state["last_topic"] <= self.context_ttl and WEAK.search(text))
        if not related:
            return None
        state["last_topic"] = now
        state["messages"].append(text[:1500])
        if state["busy"]:
            return None
        question = bool(QUESTION.search(text))
        if not question and now - state["last_reply"] < self.cooldown:
            return None
        state["busy"] = True
        return "question" if question else "discussion"

    def finish(self, key: str, *, sent: bool = True):
        state = self.sessions.get(key)
        if state:
            state["busy"] = False
            if sent:
                state["last_reply"] = time.monotonic()

    def history(self, key: str) -> str:
        state = self.sessions.get(key)
        if not state or time.monotonic() - state["last_topic"] > self.context_ttl:
            return ""
        return "\n".join(state["messages"])
