from typing import cast

from fastapi import Request

from someip_agent.state import ApplicationState


def get_state(request: Request) -> ApplicationState:
    return cast(ApplicationState, request.app.state.container)
