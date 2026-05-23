"""AIEA - 嗅探器基类和注册表"""

from abc import ABC, abstractmethod

from ..models import SessionTimeline, WasteFinding


class Sniffer(ABC):
    """嗅探器基类。每个嗅探器分析一个 Session 的时间线，输出 WasteFinding 列表。"""

    @abstractmethod
    def sniff(self, timeline: SessionTimeline) -> list[WasteFinding]:
        ...


# 嗅探器注册表
_registry: dict[str, type[Sniffer]] = {}


def register(name: str):
    """装饰器：注册嗅探器"""
    def wrapper(cls):
        _registry[name] = cls
        return cls
    return wrapper


def get_all_sniffers() -> dict[str, Sniffer]:
    """实例化所有已注册的嗅探器"""
    return {name: cls() for name, cls in _registry.items()}


def run_all(timeline: SessionTimeline) -> list[WasteFinding]:
    """在单个 SessionTimeline 上运行所有嗅探器"""
    findings = []
    for name, sniffer in get_all_sniffers().items():
        try:
            results = sniffer.sniff(timeline)
            for r in results:
                r.details["sniffer"] = name
            findings.extend(results)
        except Exception as e:
            findings.append(WasteFinding(
                severity="low",
                category="sniffer_error",
                message=f"嗅探器 {name} 执行异常: {e}",
            ))
    return findings


# Import built-in sniffers so their @register decorators populate the registry.
from . import bloated_context, death_loop, toxic_file  # noqa: E402,F401
