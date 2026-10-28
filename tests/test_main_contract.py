"""Plugin command integration with explicit AstrBot interface doubles.

These exercise our actual entry point, not a live installed AstrBot server.
The interfaces follow the checked AstrBot 4.29.3 source contract.
"""
import importlib.util
import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT=Path(__file__).resolve().parents[1]


class ImageDouble:
    def __init__(self,path="fixture.png"):
        self.path=path
    async def convert_to_file_path(self):
        return self.path


class ReplyDouble:
    def __init__(self,chain):
        self.chain=chain


class FileDouble:
    def __init__(self,name,file="",url=""):
        self.name,self.file,self.url=name,file,url


class EventDouble:
    def __init__(self,text="",*,sender="123",origin="qq:test:group:1",admin=False,images=None):
        self.message_str=text
        self.unified_msg_origin=origin
        self.sender=sender
        self.admin=admin
        self.sent=[]
        self.stopped=False
        self.message_obj=types.SimpleNamespace(message_id="msg-1")
        self.messages=images or []
    def get_sender_id(self): return self.sender
    def get_self_id(self): return "bot-1"
    def get_group_id(self): return "group-1"
    def is_admin(self): return self.admin
    def get_messages(self): return self.messages
    def stop_event(self): self.stopped=True
    def plain_result(self,text): return ("text",text)
    def image_result(self,path): return ("image",path)
    def chain_result(self,chain): return ("chain",chain)
    async def send(self,result): self.sent.append(result)


def framework_doubles(data_path):
    names=["astrbot","astrbot.api","astrbot.api.event","astrbot.api.message_components",
           "astrbot.api.provider","astrbot.api.star","astrbot.core","astrbot.core.agent",
           "astrbot.core.agent.tool","astrbot.core.agent.message"]
    modules={name:types.ModuleType(name) for name in names}
    class Star:
        def __init__(self,context): self.context=context
    class StarTools:
        @staticmethod
        def get_data_dir(name):
            data_path.mkdir(parents=True,exist_ok=True)
            return data_path
    def decorator(*args,**kwargs): return lambda fn:fn
    filt=types.SimpleNamespace(command=decorator,llm_tool=decorator,on_llm_request=decorator,
        event_message_type=decorator,EventMessageType=types.SimpleNamespace(ALL=1))
    modules["astrbot.api"].AstrBotConfig=dict
    modules["astrbot.api"].logger=logging.getLogger("captain-test")
    modules["astrbot.api.event"].AstrMessageEvent=EventDouble
    modules["astrbot.api.event"].filter=filt
    mc=modules["astrbot.api.message_components"]
    mc.Image,mc.Reply,mc.File=ImageDouble,ReplyDouble,FileDouble
    modules["astrbot.api.provider"].ProviderRequest=types.SimpleNamespace
    star=modules["astrbot.api.star"]
    star.Context,star.Star,star.StarTools=object,Star,StarTools
    class ToolSet:
        def __init__(self,tools=None): self.tools=tools or []
        def __bool__(self): return bool(self.tools)
    modules["astrbot.core.agent.tool"].ToolSet=ToolSet
    class TextPart:
        def __init__(self,text): self.text=text
        def mark_as_temp(self): return self
    modules["astrbot.core.agent.message"].TextPart=TextPart
    package=types.ModuleType("_captain_contract")
    package.__path__=[str(ROOT)]
    modules["_captain_contract"]=package
    return modules


class MainContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patch=patch.dict(sys.modules,framework_doubles(Path(self.tmp.name)))
        self.patch.start()
        spec=importlib.util.spec_from_file_location("_captain_contract.main",ROOT/"main.py")
        self.module=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=self.module
        spec.loader.exec_module(self.module)
        response=types.SimpleNamespace(completion_text="已依据可见数据给出训练建议。")
        self.context=types.SimpleNamespace(get_current_chat_provider_id=AsyncMock(return_value="current-model"),
            get_llm_tool_manager=lambda:types.SimpleNamespace(func_list=[]),
            llm_generate=AsyncMock(return_value=response),tool_loop_agent=AsyncMock(return_value=response))
        self.config={k:v.get("default") for k,v in json.loads((ROOT/"_conf_schema.json").read_text("utf-8")).items()}
        self.plugin=self.module.WowsCaptain(self.context,self.config)

    async def asyncTearDown(self):
        await self.plugin.terminate()
        self.patch.stop()
        self.tmp.cleanup()

    async def test_quoted_arguments_and_options_preserve_player_name(self):
        args,opts=self.module.parse_args('/wows 战绩 asia "Player Name" --mode pvp_solo')
        self.assertEqual(args,["战绩","asia","Player Name"])
        self.assertEqual(opts,{"mode":"pvp_solo"})
        with self.assertRaises(ValueError): self.module.parse_args('/wows 战绩 "unclosed')

    async def test_bind_stores_real_resolved_id_and_outputs_chart(self):
        event=EventDouble("wows 绑定 asia Actual_Player")
        self.plugin.wg.search_players=AsyncMock(return_value=[{"account_id":42,"nickname":"Actual_Player"}])
        self.plugin.wg.account=AsyncMock(return_value={"nickname":"Actual_Player","statistics":{"pvp":{"battles":10,"wins":6,"damage_dealt":600000}}})
        await self.plugin.wows_command(event)
        binding=self.plugin.storage.get_binding(self.plugin._scope(event))
        self.assertEqual(binding["account_id"],42)
        self.assertTrue(event.stopped)
        image=next(result[1] for result in event.sent if result[0]=="image")
        self.assertTrue(Path(image).is_file())

    async def test_recent_without_baseline_has_no_fabricated_chart(self):
        event=EventDouble("/wows 近期 7")
        self.plugin.storage.bind(self.plugin._scope(event),"asia",42)
        self.plugin.wg.account=AsyncMock(return_value={"nickname":"Actual_Player","statistics":{"pvp":{"battles":10,"wins":5}}})
        await self.plugin.wows_command(event)
        self.assertFalse(any(r[0]=="image" for r in event.sent))
        self.assertIn("首份快照","".join(r[1] for r in event.sent))

    async def test_quoted_image_uses_vision_model_and_deduplicates(self):
        self.config["vision_provider_id"]="vision-model"
        img=ImageDouble("fixture.png")
        event=EventDouble("/wows 分析",images=[img,ReplyDouble([img])])
        await self.plugin.wows_command(event)
        kwargs=self.context.llm_generate.await_args.kwargs
        self.assertEqual(kwargs["chat_provider_id"],"vision-model")
        self.assertEqual(kwargs["image_urls"],["fixture.png"])
        self.assertIn("本次实际收到的图片数量",kwargs["prompt"])

    async def test_failed_model_request_reports_configuration_without_private_error(self):
        self.context.llm_generate.side_effect=RuntimeError("private-api-token")
        event=EventDouble("/wows 分析 这份战绩")
        await self.plugin.wows_command(event)
        result="".join(r[1] for r in event.sent)
        self.assertIn("模型调用没有成功",result)
        self.assertNotIn("private-api-token",result)

    async def test_admin_session_control_is_persistent_and_nonadmin_cannot_change_it(self):
        event=EventDouble("/wows 主动 关")
        await self.plugin.wows_command(event)
        self.assertTrue(self.plugin._auto_enabled(event))
        event.admin=True
        await self.plugin.wows_command(event)
        self.assertFalse(self.plugin._auto_enabled(event))
        self.assertTrue(self.plugin.runtime_file.is_file())

    async def test_listener_ignores_own_messages_and_commands(self):
        own=EventDouble("战舰世界怎么打",sender="bot-1")
        command=EventDouble("/wows 战绩")
        await self.plugin.topic_listener(own)
        await self.plugin.topic_listener(command)
        self.context.llm_generate.assert_not_awaited()
        user=EventDouble("战舰世界驱逐舰怎么控点？")
        await self.plugin.topic_listener(user)
        self.context.llm_generate.assert_awaited_once()
        self.assertTrue(user.sent)

    async def test_haojian_unavailable_falls_back_to_official_encyclopedia(self):
        self.plugin.haojian.search=AsyncMock(side_effect=self.module.HaojianError("temporary"))
        self.plugin.wg.ships=AsyncMock(return_value=[{"ship_id":123,"name":"Yamato"}])
        ship=await self.plugin._ship("Yamato","eu")
        self.assertEqual(ship["ship_id"],123)

    async def test_kokomi_forwarding_uses_actual_client_contract_and_sender_identity(self):
        self.config["kokomi_bridge_url"]="http://127.0.0.1:19090"
        from _captain_contract.core.kokomi import KokomiClient
        with patch.object(KokomiClient,"send",new_callable=AsyncMock,return_value={"type":"msg","msg":"original engine result"}) as send:
            with patch.dict("os.environ",{"WOWS_KOKOMI_BRIDGE_TOKEN":"test-only-not-a-real-secret"}):
                event=EventDouble("/wws asia top Yamato")
                await self.plugin.kokomi_command(event)
        self.assertEqual(send.await_args.args,("wws asia top Yamato","123"))
        self.assertEqual(send.await_args.kwargs["channel_id"],"group-1")
        self.assertEqual(event.sent[-1],("text","original engine result"))

    async def test_ship_detail_exports_all_original_fields(self):
        source={"summary":{"name":"Fixture","index":"TEST123"},"version":"test","url":"https://iwarship.net/wowsdb/index",
            "comparison":{"artillery":[{"shotDelay":30}],"hull":[]},"raw":{"private_test_full_module":{"A":1}}}
        self.plugin.haojian.detail=AsyncMock(return_value=source)
        event=EventDouble("/wows 浩舰详情 TEST123 主炮")
        await self.plugin.wows_command(event)
        attached=next(x[1][0] for x in event.sent if x[0]=="chain")
        self.assertEqual(json.loads(Path(attached.file).read_text("utf-8")),source)


if __name__=="__main__":
    unittest.main()
