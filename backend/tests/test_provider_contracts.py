"""Provider wire contracts: all SDK calls are mocked; no API keys or paid calls."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.llm.base import PROVIDERS, build_llm
from app.llm.openai_llm import OpenAICompatLLM


def response(text="done", tool=False):
    calls = [SimpleNamespace(id="call-fixture", function=SimpleNamespace(
        name="fixture", arguments='{"id": 1}'))] if tool else []
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=calls))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )


class ProviderContracts(unittest.IsolatedAsyncioTestCase):
    def make(self, model="gpt-5", provider="openai", base_url="https://api.openai.com/v1", effort="high"):
        create = AsyncMock(return_value=response())
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch("app.llm.openai_llm.AsyncOpenAI", return_value=client):
            llm = OpenAICompatLLM(model=model, provider=provider, base_url=base_url,
                                  api_key="FAKE_TEST_ONLY", effort=effort)
        return llm, create

    async def run_loop(self, llm, **kwargs):
        emit = AsyncMock()
        out = await llm.agent_loop(
            system="Authorized fixture only", kickoff="inspect fixture", tools=[],
            dispatch=AsyncMock(return_value=("ok", False)), emit=emit,
            should_stop=lambda: False, max_iterations=3, **kwargs,
        )
        return out, emit

    async def test_native_reasoning_models_use_completion_budget(self):
        for name in ("gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-2025-08-07", "o1", "o3", "o4-mini"):
            with self.subTest(model=name):
                llm, create = self.make(model=name, effort="medium")
                self.assertEqual(await llm.complete("system", "user"), "done")
                sent = create.call_args.kwargs
                self.assertEqual(sent["max_completion_tokens"], 4000)
                self.assertEqual(sent["reasoning_effort"], "medium")
                self.assertNotIn("max_tokens", sent)

    async def test_complete_and_loop_share_capability_contract(self):
        llm, create = self.make(effort="low")
        usage = AsyncMock()
        await llm.complete("system", "user", on_usage=usage)
        complete = create.call_args.kwargs
        await self.run_loop(llm, on_usage=usage)
        loop = create.call_args.kwargs
        self.assertEqual(complete["reasoning_effort"], loop["reasoning_effort"])
        self.assertEqual(loop["max_completion_tokens"], 8000)
        self.assertNotIn("max_tokens", loop)
        self.assertEqual(usage.await_args_list[0].args, (11, 7))
        self.assertEqual(usage.await_count, 2)

    async def test_ordinary_native_model_keeps_compatible_parameters(self):
        llm, create = self.make(model="gpt-4o")
        await llm.complete("system", "user")
        self.assertEqual(create.call_args.kwargs["max_tokens"], 4000)
        self.assertNotIn("reasoning_effort", create.call_args.kwargs)
        _, emit = await self.run_loop(llm, get_effort=lambda: "max")
        self.assertEqual(create.call_args.kwargs["max_tokens"], 8000)
        notice = [c.args[2]["text"] for c in emit.await_args_list if c.args[1] == "Model capability"]
        self.assertEqual(len(notice), 1)
        self.assertIn("not forwarded", notice[0])

    async def test_model_name_does_not_enable_vendor_knobs_on_gateways(self):
        cases = [
            ("custom", "http://localhost:11434/v1", "gpt-5"),
            ("custom", "https://api.openai.com/v1", "o4-mini"),
            ("openrouter", "https://openrouter.ai/api/v1", "openai/gpt-5"),
            ("openai", "https://gateway.example.test/v1", "gpt-5"),
        ]
        for provider, url, model in cases:
            with self.subTest(provider=provider, base=url):
                llm, create = self.make(provider=provider, base_url=url, model=model)
                await llm.complete("system", "user")
                self.assertEqual(create.call_args.kwargs["max_tokens"], 4000)
                self.assertNotIn("max_completion_tokens", create.call_args.kwargs)
                self.assertNotIn("reasoning_effort", create.call_args.kwargs)
                await self.run_loop(llm, get_effort=lambda: "max")
                self.assertNotIn("reasoning_effort", create.call_args.kwargs)

    async def test_only_official_secure_origin_gets_native_contract(self):
        urls = ["https://api.openai.com.evil.test/v1", "http://api.openai.com/v1",
                "https://user@api.openai.com/v1", "https://api.openai.com/v2"]
        for url in urls:
            with self.subTest(url=url):
                llm, create = self.make(base_url=url)
                await llm.complete("system", "user")
                self.assertNotIn("reasoning_effort", create.call_args.kwargs)

    async def test_effort_callback_is_read_for_each_model_request(self):
        llm, create = self.make()
        create.side_effect = [response("first", tool=True), response("second", tool=True), response("done")]
        get_effort = Mock(side_effect=["low", "max", "medium"])
        _, emit = await self.run_loop(llm, get_effort=get_effort)
        sent = [c.kwargs["reasoning_effort"] for c in create.await_args_list]
        self.assertEqual(sent, ["low", "high", "medium"])
        self.assertEqual(get_effort.call_count, 3)
        self.assertTrue(any("maps to 'high'" in str(c.args) for c in emit.await_args_list))

    async def test_invalid_native_effort_fails_before_api_call(self):
        llm, create = self.make(effort="unrecognized")
        with self.assertRaisesRegex(ValueError, "Unsupported reasoning effort"):
            await llm.complete("system", "user")
        create.assert_not_awaited()

    async def test_legacy_o1_and_chat_variants_do_not_get_effort(self):
        for name in ("o1-mini", "o1-preview", "gpt-5-chat-latest"):
            with self.subTest(model=name):
                llm, create = self.make(model=name)
                await llm.complete("system", "user")
                self.assertEqual(create.call_args.kwargs["max_completion_tokens"], 4000)
                self.assertNotIn("reasoning_effort", create.call_args.kwargs)


class BuildProviderContracts(unittest.TestCase):
    def cfg(self):
        return {"provider": "openai", "model": "gpt-5", "effort": "medium",
                "openai_api_key": "OPENAI_FAKE", "openai_base_url": "https://api.openai.com/v1",
                "custom_api_key": "", "custom_base_url": "", "openrouter_api_key": "ROUTER_FAKE"}

    def test_custom_owns_its_key_setting(self):
        self.assertEqual(PROVIDERS["custom"]["key_setting"], "custom_api_key")

    def test_blank_custom_cannot_inherit_openai_secret_or_endpoint(self):
        with patch("app.llm.openai_llm.OpenAICompatLLM") as constructor:
            build_llm(self.cfg(), provider="custom", model="local-model")
        sent = constructor.call_args.kwargs
        self.assertEqual(sent["base_url"], "http://localhost:11434/v1")
        self.assertIsNone(sent["api_key"])
        self.assertEqual(sent["provider"], "custom")

    def test_explicit_custom_only_uses_custom_credentials(self):
        cfg = self.cfg()
        cfg.update(custom_api_key="CUSTOM_FAKE", custom_base_url="https://custom.example.test/v1")
        with patch("app.llm.openai_llm.OpenAICompatLLM") as constructor:
            build_llm(cfg, provider="custom", model="gpt-5")
        self.assertEqual(constructor.call_args.kwargs["api_key"], "CUSTOM_FAKE")
        self.assertEqual(constructor.call_args.kwargs["base_url"], "https://custom.example.test/v1")

    def test_openrouter_only_uses_openrouter_credentials(self):
        with patch("app.llm.openai_llm.OpenAICompatLLM") as constructor:
            build_llm(self.cfg(), provider="openrouter", model="openai/gpt-5")
        self.assertEqual(constructor.call_args.kwargs["api_key"], "ROUTER_FAKE")
        self.assertEqual(constructor.call_args.kwargs["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(constructor.call_args.kwargs["effort"], "medium")

    def test_native_provider_and_effort_reach_adapter(self):
        with patch("app.llm.openai_llm.OpenAICompatLLM") as constructor:
            build_llm(self.cfg())
        self.assertEqual(constructor.call_args.kwargs["provider"], "openai")
        self.assertEqual(constructor.call_args.kwargs["effort"], "medium")

    def test_unknown_provider_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported provider"):
            build_llm(self.cfg(), provider="typo-vendor")


if __name__ == "__main__":
    unittest.main()
