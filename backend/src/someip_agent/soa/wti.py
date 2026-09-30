"""SAT WTI 辅助断言：只编排已存在的事件观测和 Getter，不实现协议数据面。"""

from __future__ import annotations

from typing import Any, Protocol, cast

WTI_SERVICE_CLIENT = "WTIService_client"
WTIAUTODRIVE_SERVICE_CLIENT = "WTIAutoDriveService_client"


class WTIHost(Protocol):
    def ck_event_and_resp(
        self, partner_key: str, event_name: str, event_info: dict[str, Any], *, timeout: float
    ) -> bool: ...
    def ck_coming_event_and_resp(
        self,
        partner_key: str,
        event_name: str,
        event_info: dict[str, Any],
        *,
        timeout: float,
        deviation: float,
    ) -> bool: ...
    def ck_no_specific_event(
        self, partner_key: str, interface_name: str, hint: str, timeout: float
    ) -> bool: ...
    def send_request_and_ck_resp(
        self,
        partner_key: str,
        method_name: str,
        args: dict[str, Any],
        ck_info: dict[str, Any],
        *,
        timeout: float,
    ) -> bool: ...


class WTIAssertions:
    @staticmethod
    def _wti_key(wti_auto: bool) -> str:
        return WTIAUTODRIVE_SERVICE_CLIENT if wti_auto else WTI_SERVICE_CLIENT

    def _wti_host(self) -> WTIHost:
        return cast(WTIHost, self)

    def ck_wti_warning_and_resp(
        self, hint: str, info: int | str, timeout: float = 1, wti_auto: bool = False
    ) -> bool:
        return self._wti_host().ck_event_and_resp(
            self._wti_key(wti_auto),
            "WarningMsgList",
            {"list": [{"name": hint, "info": str(info)}]},
            timeout=timeout,
        )

    def ck_wti_telltale_and_resp(
        self, hint: str, state: int | str, timeout: float = 1, wti_auto: bool = False
    ) -> bool:
        return self._wti_host().ck_event_and_resp(
            self._wti_key(wti_auto),
            "TelltaleList",
            {"list": [{"name": hint, "state": str(state)}]},
            timeout=timeout,
        )

    def ck_wti_coming_warning_and_resp(
        self,
        hint: str,
        info: int | str,
        timeout: float = 1,
        deviation: float = 0,
        wti_auto: bool = False,
    ) -> bool:
        return self._wti_host().ck_coming_event_and_resp(
            self._wti_key(wti_auto),
            "WarningMsgList",
            {"list": [{"name": hint, "info": str(info)}]},
            timeout=timeout,
            deviation=deviation,
        )

    def ck_wti_coming_telltale_and_resp(
        self,
        hint: str,
        state: int | str,
        timeout: float = 1,
        deviation: float = 0,
        wti_auto: bool = False,
    ) -> bool:
        return self._wti_host().ck_coming_event_and_resp(
            self._wti_key(wti_auto),
            "TelltaleList",
            {"list": [{"name": hint, "state": str(state)}]},
            timeout=timeout,
            deviation=deviation,
        )

    def ck_wti_no_warning_and_ck_resp(
        self, hint: str, info: int | str, timeout: float = 1, wti_auto: bool = False
    ) -> bool:
        host, key = self._wti_host(), self._wti_key(wti_auto)
        host.ck_no_specific_event(key, "WarningMsgList", hint, timeout)
        return host.send_request_and_ck_resp(
            key, "GetWarningMsgList", {}, {"out": [{"name": hint, "info": str(info)}]}, timeout=0.2
        )

    def ck_wti_no_telltale_and_ck_resp(
        self, hint: str, info: int | str, timeout: float = 1, wti_auto: bool = False
    ) -> bool:
        host, key = self._wti_host(), self._wti_key(wti_auto)
        host.ck_no_specific_event(key, "TelltaleList", hint, timeout)
        return host.send_request_and_ck_resp(
            key, "GetTelltaleList", {}, {"out": [{"name": hint, "state": str(info)}]}, timeout=0.2
        )
