"""有界时间状态图契约；只校验配置，不调度、不执行表达式。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr


class TimedSignalState(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    value: StrictInt | StrictFloat | StrictBool | StrictStr
    duration_ms: StrictInt | None = Field(default=None, ge=1, le=18446744073709551615)
    next: StrictStr | None = Field(default=None, max_length=64)


def validate_timed_states(
    kind: str, initial_state: str | None, states: list[TimedSignalState]
) -> None:
    if kind != "state_machine":
        if initial_state is not None or states:
            raise ValueError("非状态机源不能包含状态图")
        return
    if not initial_state or not states:
        raise ValueError("时间状态机必须提供 initial_state 与 1–128 个状态")
    mapping = {state.name: state for state in states}
    if len(mapping) != len(states) or initial_state not in mapping:
        raise ValueError("状态名必须唯一，初始状态必须存在")
    for state in states:
        if state.next is None:
            if state.duration_ms is not None:
                raise ValueError("终态不设置 duration_ms，进入后保持末值")
        elif state.duration_ms is None or state.next not in mapping:
            raise ValueError("转换必须有正整型毫秒持续时间与存在的下一状态")
    seen: set[str] = set()
    name: str | None = initial_state
    elapsed = 0
    while name is not None and name not in seen:
        seen.add(name)
        state = mapping[name]
        if state.duration_ms is not None:
            elapsed += state.duration_ms
            if elapsed > 18446744073709551615:
                raise ValueError("状态图累计时间超出 uint64 毫秒范围")
        name = state.next
    if len(seen) != len(states):
        raise ValueError("状态图包含从初态不可达的状态")
