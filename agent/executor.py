from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any

from .errors import (
    ErrorCode,
    ToolError,
    classify_exception,
    classify_payload,
)
from .execution import (
    ExecutionRecord,
    ExecutionStatus,
    new_execution_id,
    redact_arguments,
    utc_now_iso,
)
from .permissions import (
    ApprovalHandler,
    PermissionPolicy,
    ToolBudget,
    default_approval,
    default_budget,
    default_policy,
)
from .registry import ToolRegistry
from .schemas import ToolDefinition

"""
Tool executor (Phase 2.5, 2.6, 2.7, 2.8).

This is the single place where a tool actually runs. Everything the
agent loop used to do inline - call the tool, catch whatever blew up,
stuff a string back into the message list - now happens here, with
structure.

The pipeline, exactly as Phase2.md section 2.5 specifies:

    tool call
       |
       +-- 1. resolve the tool          (does it exist?)
       +-- 2. check availability        (was it routed for this turn?)
       +-- 3. validate arguments        (before we touch the network)
       +-- 4. check permission          (may this agent do this?)
       +-- 5. request approval          (may we do it right now?)
       +-- 6. execute, with timeout
       +-- 7. classify any failure
       +-- 8. retry if - and only if - it is safe
       |
       v
    ExecutionRecord

Order is not arbitrary. Steps 1-5 are all free: they need no network,
no credentials, and no waiting. Doing them first means a
mis-spelled tool name costs microseconds instead of a 30 second
timeout, and a forbidden action is refused without ever leaving the
process.

The general rule: reject as early and as cheaply as you can.
"""


try:
    from jsonschema import Draft7Validator

    _HAS_JSONSCHEMA = True

except ImportError:  # pragma: no cover
    _HAS_JSONSCHEMA = False


# ---------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------

# Per-attempt ceiling. Not per-execution: with retries a call can take
# up to roughly max_attempts * this, plus backoff.
DEFAULT_TIMEOUT_SECONDS = 30.0

# Total attempts, not retries. 3 means "try, retry, retry".
DEFAULT_MAX_ATTEMPTS = 3

# Exponential backoff: 0.5s, 1s, 2s, 4s ... capped.
DEFAULT_BACKOFF_BASE = 0.5
DEFAULT_BACKOFF_MAX = 8.0

# Fraction of the delay added as random jitter.
#
# Without jitter, every failing call in the system retries at exactly
# the same moments. When a service recovers from an outage it gets hit
# by a synchronized wall of retries and falls over again. This is
# called a thundering herd, and a little randomness is the whole fix.
BACKOFF_JITTER = 0.25


class ToolExecutor:
    """
    Executes MCP tools safely and records what happened.

    Holds no per-call state. Everything a call needs is passed in, and
    everything it produces comes back in the return value. That is what
    makes it safe to share one executor across many concurrent
    conversations in Phase 3.
    """

    __slots__ = (
        "registry",
        "policy",
        "approval",
        "budget",
        "timeout_seconds",
        "max_attempts",
        "backoff_base",
        "backoff_max",
        "validate_arguments",
        "coerce_types",
    )

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        policy: PermissionPolicy | None = None,
        approval: ApprovalHandler | None = None,
        budget: ToolBudget | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_max: float = DEFAULT_BACKOFF_MAX,
        validate_arguments: bool = True,
        coerce_types: bool = True,
    ) -> None:

        self.registry = registry

        # `is None`, NOT `policy or default_policy()`.
        #
        # `or` asks whether the object is TRUTHY, and a policy object
        # that defines __len__ is falsy when it is empty. So an agent
        # configured with zero enabled tools - the most locked-down
        # agent possible - would have its policy silently replaced by
        # AllowAllPolicy and be allowed EVERYTHING.
        #
        # A fail-open bug, produced by a Python idiom that looks
        # perfectly ordinary. Caught by
        # tests/permissions/test_executor_gate.py.
        self.policy = default_policy() if policy is None else policy
        self.approval = (
            default_approval() if approval is None else approval
        )

        # `is None`, for the same reason as the two above: a budget
        # object that reports "zero remaining" must not be mistaken for
        # "no budget configured" by a truthiness check.
        self.budget = default_budget() if budget is None else budget
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.validate_arguments = validate_arguments
        self.coerce_types = coerce_types

    # -----------------------------------------------------------------
    # Entry point
    # -----------------------------------------------------------------

    async def execute(
        self,
        session: Any,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        allowed_tools: set[str] | None = None,
    ) -> ExecutionRecord:
        """
        Run one tool call and return a full record of what happened.

        Never raises for a tool failure. A failure is a value the agent
        must be able to read and react to - see the long comment at the
        top of errors.py.

        The one exception is `asyncio.CancelledError`, which is
        re-raised untouched, because cancellation means the caller has
        already given up and we must not keep working.
        """

        started_at = utc_now_iso()
        started = time.perf_counter()

        arguments = arguments or {}

        execution_id = new_execution_id()

        # Type fixes applied to the model's arguments, recorded so the
        # timeline shows them instead of silently rewriting the call.
        coerced_note: list[str] = []

        def build(
            *,
            status: ExecutionStatus,
            tool: ToolDefinition | None = None,
            result: Any = None,
            error: ToolError | None = None,
            attempts: int = 1,
            approved: bool | None = None,
        ) -> ExecutionRecord:
            """
            Assemble the record. Nested so every exit path from this
            method produces the SAME shape, with timing filled in
            automatically. A function with eight return statements is
            a function where one of them will eventually forget a
            field.
            """

            return ExecutionRecord(
                execution_id=execution_id,
                tool_name=tool_name,
                status=status,
                started_at=started_at,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                attempts=attempts,
                server=tool.server if tool else "",
                namespace=tool.namespace if tool else None,
                operation=tool.operation.value if tool else "",
                risk_level=tool.risk_level.value if tool else "",
                arguments=redact_arguments(arguments),
                result=result,
                error=error,
                approved_by_user=approved,
                coercions=tuple(coerced_note),
            )

        # --- 1. Does the tool exist? ---------------------------------

        tool = self.registry.get(tool_name)

        if tool is None:
            return build(
                status=ExecutionStatus.FAILED,
                error=ToolError(
                    code=ErrorCode.TOOL_NOT_FOUND,
                    message=(
                        f"No tool named '{tool_name}' is registered."
                    ),
                ),
            )

        # --- 2. Was it offered for this turn? ------------------------
        #
        # LLMs occasionally invent a tool name, or call one they saw
        # earlier in the conversation but which the router did not
        # select this time. Catching that here keeps the router's
        # decision meaningful instead of advisory.

        if allowed_tools is not None and tool_name not in allowed_tools:
            return build(
                status=ExecutionStatus.FAILED,
                tool=tool,
                error=ToolError(
                    code=ErrorCode.TOOL_NOT_AVAILABLE,
                    message=(
                        f"'{tool_name}' was not selected for this "
                        "request."
                    ),
                ),
            )

        # --- 3. Are the arguments valid? -----------------------------
        #
        # Repair before judging. The model sending "1" instead of 1 is
        # a formatting slip, not a mistake about the task, and burning
        # an agent round on it helps nobody.

        if self.coerce_types:

            arguments, coercions = self.coerce_arguments(
                tool,
                arguments,
            )

            coerced_note = coercions

        if self.validate_arguments:

            invalid = self._validate_arguments(tool, arguments)

            if invalid is not None:
                return build(
                    status=ExecutionStatus.FAILED,
                    tool=tool,
                    error=invalid,
                )

        # --- 4. Is this agent permitted? -----------------------------

        decision = self.policy.check(tool)

        if not decision.allowed:
            return build(
                status=ExecutionStatus.DENIED,
                tool=tool,
                error=ToolError(
                    code=ErrorCode.PERMISSION_DENIED,
                    message=decision.reason,
                ),
            )

        # --- 4b. Is there budget left? -------------------------------
        #
        # AFTER permission, BEFORE approval, and both halves of that
        # matter.
        #
        # After permission, so a call that was going to be refused
        # anyway does not spend budget - otherwise a broken agent
        # asking for a tool it cannot use would exhaust the user's
        # hourly allowance and take the working tools down with it.
        #
        # Before approval, so a human is never asked to authorise
        # something that is going to be refused the moment they say
        # yes. Nothing is more corrosive to an approval prompt than
        # discovering it did not mean anything.
        budget = await self.budget.check(tool)

        if not budget.allowed:
            return build(
                status=ExecutionStatus.DENIED,
                tool=tool,
                error=ToolError(
                    code=ErrorCode.BUDGET_EXCEEDED,
                    message=budget.reason,
                    retry_after=budget.retry_after or None,
                ),
            )

        # --- 5. Does a human need to say yes? ------------------------

        approved: bool | None = None

        # ASK THE HANDLER, do not decide here.
        #
        # This used to read `if tool.requires_approval:` - the value
        # Phase 2 classified from the tool's operation and risk. That
        # is the right DEFAULT, but it is not the user's setting, and
        # agent_tools.requires_approval was therefore stored, shown in
        # the UI, and never consulted.
        #
        # The handler knows the agent's configuration; the executor
        # does not, and should not. Every built-in handler answers this
        # with tool.requires_approval, so behaviour is unchanged for
        # them.
        if self.approval.requires(tool):

            approved = await self.approval.request(tool, arguments)

            if not approved:
                return build(
                    status=ExecutionStatus.DENIED,
                    tool=tool,
                    error=ToolError(
                        code=ErrorCode.APPROVAL_DENIED,
                        message=(
                            "The user declined this action."
                        ),
                    ),
                    approved=False,
                )

        # --- 6-8. Execute, classify, retry ---------------------------

        result, error, attempts = await self._call_with_retries(
            session=session,
            tool=tool,
            arguments=arguments,
        )

        return build(
            status=(
                ExecutionStatus.SUCCESS
                if error is None
                else ExecutionStatus.FAILED
            ),
            tool=tool,
            result=result,
            error=error,
            attempts=attempts,
            approved=approved,
        )

    # -----------------------------------------------------------------
    # Argument validation
    # -----------------------------------------------------------------

    @staticmethod
    def coerce_arguments(
        tool: ToolDefinition,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Fix argument types the model got *almost* right.

        LLMs produce JSON as text, and they routinely send the string
        "1" where the schema asks for the integer 1. That is not the
        model being wrong about the task - it knew exactly what it
        wanted - so failing the call over it wastes an entire agent
        round to relearn something we already know.

        Seen in a real session:

            google_drive_search_files
              -> page_size: '1' is not of type 'integer'

        The rules are strictly schema-driven and lossless. We only
        convert when the schema names the target type AND the value
        converts cleanly:

            "1"      -> 1        (integer field)
            "2.5"    -> 2.5      (number field)
            "true"   -> True     (boolean field)
            42       -> "42"     (string field)
            "abc"    -> ["abc"]  (array field)

        Anything ambiguous is left alone for validation to reject with
        a clear message. Coercion must never guess at MEANING - only at
        representation. "yes" does not become True for an integer
        field, and "abc" never becomes a number.

        Returns the corrected arguments plus a list of what changed, so
        the execution record can show it rather than silently editing
        the model's request.
        """

        schema = tool.input_schema

        if not isinstance(schema, dict):
            return arguments, []

        properties = schema.get("properties")

        if not isinstance(properties, dict):
            return arguments, []

        coerced = dict(arguments)
        notes: list[str] = []

        for name, value in arguments.items():

            definition = properties.get(name)

            if not isinstance(definition, dict):
                continue

            expected = definition.get("type")

            # Schemas like {"type": ["string", "null"]} allow several
            # types; if the value already fits one of them there is
            # nothing to fix.
            if isinstance(expected, list):
                expected = next(
                    (
                        item
                        for item in expected
                        if item != "null"
                    ),
                    None,
                )

            if not isinstance(expected, str):
                continue

            new_value = ToolExecutor._coerce_one(expected, value)

            if new_value is not None:
                coerced[name] = new_value
                notes.append(
                    f"{name}: {type(value).__name__} -> {expected}"
                )

        return coerced, notes

    @staticmethod
    def _coerce_one(expected: str, value: Any) -> Any | None:
        """
        Convert one value, or return None to mean "leave it alone".
        """

        # bool is a subclass of int in Python, so it must be tested
        # first or True would sail through the integer branch.
        if expected == "boolean":

            if isinstance(value, bool):
                return None

            if isinstance(value, str):
                lowered = value.strip().lower()

                if lowered in {"true", "yes"}:
                    return True

                if lowered in {"false", "no"}:
                    return False

            return None

        if expected == "integer":

            if isinstance(value, bool) or isinstance(value, int):
                return None

            if isinstance(value, float) and value.is_integer():
                return int(value)

            if isinstance(value, str):
                try:
                    return int(value.strip())
                except ValueError:
                    return None

            return None

        if expected == "number":

            if isinstance(value, bool):
                return None

            if isinstance(value, (int, float)):
                return None

            if isinstance(value, str):
                try:
                    return float(value.strip())
                except ValueError:
                    return None

            return None

        if expected == "string":

            if isinstance(value, str):
                return None

            if isinstance(value, (int, float, bool)):
                return str(value)

            return None

        if expected == "array":

            if isinstance(value, list):
                return None

            if value is None:
                return None

            # A very common model mistake: sending one item where a
            # list was asked for. calendar_ids="primary" instead of
            # calendar_ids=["primary"].
            return [value]

        return None

    def _validate_arguments(
        self,
        tool: ToolDefinition,
        arguments: dict[str, Any],
    ) -> ToolError | None:
        """
        Check arguments against the tool's JSON Schema BEFORE calling.

        Why bother, when the server would reject them anyway?

          - Speed. A local check is microseconds; a round trip that
            ends in a 400 can be seconds.
          - Better messages. We can tell the model exactly which field
            was wrong and why, in one consistent format, instead of
            forwarding four services' differently-worded complaints.
          - Safety. Half-valid arguments on a write tool can do real
            damage before the server notices.

        Returns None when everything is fine.
        """

        schema = tool.input_schema

        if not isinstance(schema, dict) or not schema:
            return None

        if _HAS_JSONSCHEMA:
            problems = self._jsonschema_problems(schema, arguments)
        else:
            problems = self._basic_problems(schema, arguments)

        if not problems:
            return None

        return ToolError(
            code=ErrorCode.INVALID_ARGUMENTS,
            message=(
                f"Invalid arguments for '{tool.name}': "
                + "; ".join(problems)
            ),
            details={"problems": problems},
        )

    @staticmethod
    def _jsonschema_problems(
        schema: dict[str, Any],
        arguments: dict[str, Any],
    ) -> list[str]:
        """
        Full validation via the `jsonschema` library.

        Using the library rather than hand-rolling one: JSON Schema has
        a lot of surface (nested objects, anyOf, formats), and a
        half-correct validator that rejects VALID input is worse than
        no validator at all, because the failure looks like the agent
        being broken.
        """

        try:
            validator = Draft7Validator(schema)

        except Exception:
            # A schema we cannot even compile is the server's problem,
            # not the caller's. Do not block the call over it.
            return []

        problems: list[str] = []

        for error in sorted(
            validator.iter_errors(arguments),
            key=lambda item: list(item.path),
        ):
            location = (
                ".".join(str(part) for part in error.path)
                or "(root)"
            )

            problems.append(f"{location}: {error.message}")

            # Three is enough for the model to act on. A wall of
            # validation text just crowds the context window.
            if len(problems) >= 3:
                break

        return problems

    @staticmethod
    def _basic_problems(
        schema: dict[str, Any],
        arguments: dict[str, Any],
    ) -> list[str]:
        """
        Minimal fallback when `jsonschema` is not installed.

        Covers only required fields and top-level types - the two
        mistakes that actually happen. Deliberately conservative: it
        reports nothing it is not sure about, so it can never block a
        valid call.
        """

        problems: list[str] = []

        required = schema.get("required")

        if isinstance(required, list):
            for field_name in required:
                if field_name not in arguments:
                    problems.append(
                        f"{field_name}: required field is missing"
                    )

        properties = schema.get("properties")

        if isinstance(properties, dict):

            expected_types = {
                "string": str,
                "integer": int,
                "number": (int, float),
                "boolean": bool,
                "array": list,
                "object": dict,
            }

            for name, value in arguments.items():

                definition = properties.get(name)

                if not isinstance(definition, dict):
                    continue

                expected = expected_types.get(
                    definition.get("type", "")
                )

                if expected is None:
                    continue

                # bool is a subclass of int in Python, so an explicit
                # guard is needed or True passes as an integer.
                if (
                    expected is not bool
                    and isinstance(value, bool)
                ):
                    problems.append(
                        f"{name}: expected "
                        f"{definition.get('type')}, got boolean"
                    )
                    continue

                if not isinstance(value, expected):
                    problems.append(
                        f"{name}: expected "
                        f"{definition.get('type')}, got "
                        f"{type(value).__name__}"
                    )

        return problems[:3]

    # -----------------------------------------------------------------
    # Execution with retries
    # -----------------------------------------------------------------

    async def _call_with_retries(
        self,
        session: Any,
        tool: ToolDefinition,
        arguments: dict[str, Any],
    ) -> tuple[Any, ToolError | None, int]:
        """
        Call the tool, retrying only where it is safe to do so.

        Returns (result, error, attempts).

        The important line in this method is the retryability check.
        `ToolError.retryable()` takes the tool's OPERATION, so a
        timeout on a read is retried and the identical timeout on a
        write is not - because a timeout never tells you whether the
        write already took effect. See the long note in errors.py.
        """

        last_error: ToolError | None = None

        for attempt in range(1, self.max_attempts + 1):

            try:
                raw = await asyncio.wait_for(
                    session.call_tool(tool.name, arguments),
                    timeout=self.timeout_seconds,
                )

            except asyncio.CancelledError:
                # Someone shut us down. Never swallow this: converting
                # cancellation into a retryable error would keep work
                # alive that the caller has already abandoned.
                raise

            except Exception as exc:
                last_error = classify_exception(exc)

            else:
                result, error = self._interpret(raw)

                if error is None:
                    return result, None, attempt

                last_error = error

            # Decide whether to go round again.
            if attempt >= self.max_attempts:
                break

            if not last_error.retryable(tool.operation):
                break

            await asyncio.sleep(
                self._backoff_delay(attempt, last_error)
            )

        return None, last_error, attempt

    def _backoff_delay(
        self,
        attempt: int,
        error: ToolError,
    ) -> float:
        """
        How long to wait before the next attempt.

        Exponential, capped, and jittered:

            attempt 1 -> ~0.5s
            attempt 2 -> ~1.0s
            attempt 3 -> ~2.0s

        Exponential because if the service is struggling, hammering it
        at a fixed interval makes things worse. Capped so a retry never
        blocks a user for a minute. Jittered so that many concurrent
        agents do not all retry in lockstep.

        `retry_after` wins when the service told us how long to wait -
        an explicit instruction always beats our guess.
        """

        if error.retry_after is not None:
            return min(error.retry_after, self.backoff_max)

        delay = min(
            self.backoff_base * (2 ** (attempt - 1)),
            self.backoff_max,
        )

        return delay + random.uniform(0.0, delay * BACKOFF_JITTER)

    # -----------------------------------------------------------------
    # Interpreting the MCP response
    # -----------------------------------------------------------------

    def _interpret(
        self,
        raw: Any,
    ) -> tuple[Any, ToolError | None]:
        """
        Turn an MCP CallToolResult into (payload, error).

        There are THREE distinct ways a call can be a failure, and a
        correct executor has to check all of them. Missing any one
        means silently recording failures as successes:

          1. An exception was raised.        Handled by the caller.

          2. `isError` is set on the result. The MCP protocol's own
             error channel.

          3. The result looks fine, but the payload says
             `{"success": false, ...}`.

        Case 3 is the one that catches people out, and it is the normal
        case for YOUR server. Your `@github_tool` / `@slack_tool`
        wrappers catch exceptions and RETURN a dictionary, so as far as
        MCP is concerned the call was a complete success. The failure
        is only visible inside the payload.
        """

        payload = self._extract_payload(raw)

        if getattr(raw, "is_error", False) or getattr(
            raw, "isError", False
        ):
            error = classify_payload(payload)

            if error is not None:
                return None, error

            return None, ToolError(
                code=ErrorCode.SERVICE_ERROR,
                message=self._describe(payload),
                source="service",
            )

        error = classify_payload(payload)

        if error is not None:
            return None, error

        return payload, None

    @staticmethod
    def _extract_payload(raw: Any) -> Any:
        """
        Pull the useful data out of a CallToolResult.

        Prefers `structuredContent`, which newer MCP servers populate
        with real JSON. Falls back to concatenating the text blocks and
        parsing them, which is what your server currently produces.

        Returns the raw text when it is not JSON - a plain string
        answer is a perfectly valid tool result, not an error.
        """

        structured = getattr(raw, "structured_content", None)

        if structured is None:
            structured = getattr(raw, "structuredContent", None)

        if structured is not None:
            return structured

        content = getattr(raw, "content", None)

        if content is None:
            return raw

        texts: list[str] = []

        for block in content:

            text = getattr(block, "text", None)

            if isinstance(text, str):
                texts.append(text)

        if not texts:
            return None

        combined = "".join(texts)

        try:
            return json.loads(combined)

        except (json.JSONDecodeError, TypeError):
            return combined

    @staticmethod
    def _describe(payload: Any) -> str:

        if isinstance(payload, str):
            return payload[:500]

        if payload is None:
            return "The tool reported an error with no details."

        return json.dumps(payload, default=str)[:500]
