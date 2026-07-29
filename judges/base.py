"""
Base class for the LLM roles that read a case and return a structured verdict
(groundedness, resolution, scope). One template method holds what they share —
spec lookup, availability, the LLM call, and error semantics — subclasses define
the payload, the result shape, the stub, and what a failure means for the policy.

Every non-stub failure result carries `error = {where, what_to_do, detail}` (see
errors.py), so eval_fast can diagnose WHERE a broken run failed without regexes.
"""
import config
from errors import error_info
from judges import _llm


class LlmJudge:
    role = ""                    # config role name ([groundedness] / [resolution] / [scope])
    schema: dict = {}            # structured-output JSON schema for the model

    def spec(self) -> config.AgentSpec:
        return config.get(self.role)

    def available(self) -> bool:
        return self.spec().available()

    # ── subclass contract ──
    def payload(self, case: dict, trace: dict) -> dict:
        raise NotImplementedError

    def shape(self, out: dict) -> dict:
        """Validated model output → result dict (derive policy fields in code here)."""
        raise NotImplementedError

    def stub(self) -> dict:
        """Result when no LLM is available (keyless/offline runs)."""
        raise NotImplementedError

    def on_error(self, e: Exception) -> dict:
        """Result when the call failed; must include `error` (use `error_info(e)`)."""
        raise NotImplementedError

    # ── template ──
    async def call(self, spec: config.AgentSpec, case: dict, trace: dict) -> dict:
        return await _llm.judge_json(spec, spec.prompt_text(), self.payload(case, trace), self.schema)

    async def judge(self, case: dict, trace: dict) -> dict:
        spec = self.spec()
        if not spec.available():
            return self.stub()
        try:
            return self.shape(await self.call(spec, case, trace))
        except Exception as e:  # one bad row must not kill a batch — surface, don't crash
            return self.on_error(e)


__all__ = ["LlmJudge", "error_info"]
