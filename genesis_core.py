"""PISTOL GENESIS: single-process infrastructure scheduler."""

from dataclasses import dataclass
from threading import Lock
from typing import Callable, TypeVar

T = TypeVar("T")
BACKENDS = frozenset({"cpu", "cuda", "rocm", "vulkan"})


class NoCapacity(RuntimeError):
    """No registered node can currently accept the task."""


@dataclass
class _Node:
    name: str
    backend: str
    capacity_mb: int
    slots: int
    enabled: bool
    reserved_mb: int = 0
    active: int = 0


@dataclass(frozen=True)
class Task:
    memory_mb: int
    backends: tuple[str, ...]


class GenesisCore:
    def __init__(self):
        self._nodes: dict[str, _Node] = {}
        self._lock = Lock()

    def register(self, name: str, backend: str, capacity_mb: int,
                 slots: int = 1, enabled: bool = True) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("A node name is required")
        if not isinstance(backend, str) or backend not in BACKENDS:
            raise ValueError("Unsupported backend")
        if type(capacity_mb) is not int or capacity_mb < 0:
            raise ValueError("Invalid memory capacity")
        if type(slots) is not int or slots < 1:
            raise ValueError("Invalid task slot count")
        if type(enabled) is not bool:
            raise ValueError("Invalid enabled flag")
        with self._lock:
            if name in self._nodes:
                raise ValueError(f"Node already registered: {name}")
            self._nodes[name] = _Node(
                name, backend, capacity_mb, slots, enabled
            )

    def drain(self, name: str) -> None:
        """Stop new assignments while current callbacks finish."""
        with self._lock:
            self._nodes[name].enabled = False

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "name": n.name,
                    "backend": n.backend,
                    "enabled": n.enabled,
                    "free_memory_mb": n.capacity_mb - n.reserved_mb,
                    "available_slots": n.slots - n.active,
                    "active_tasks": n.active,
                }
                for n in self._nodes.values()
            ]

    def execute(self, task: Task, operation: Callable[[str], T]) -> T:
        if type(task.memory_mb) is not int or task.memory_mb < 0:
            raise ValueError("Invalid task memory requirement")
        if (not isinstance(task.backends, tuple) or not task.backends
                or any(not isinstance(b, str) or b not in BACKENDS
                       for b in task.backends)):
            raise ValueError("Invalid task backends")
        if not callable(operation):
            raise ValueError("An execution callback is required")
        with self._lock:
            candidates = [
                n for n in self._nodes.values()
                if n.enabled and n.backend in task.backends
                and n.active < n.slots
                and n.capacity_mb - n.reserved_mb >= task.memory_mb
            ]
            if not candidates:
                raise NoCapacity("No suitable capacity is available")
            node = min(candidates, key=lambda n: (
                n.active / n.slots,
                -(n.capacity_mb - n.reserved_mb), n.name,
            ))
            node.active += 1
            node.reserved_mb += task.memory_mb
        try:
            return operation(node.name)
        finally:
            with self._lock:
                node.active -= 1
                node.reserved_mb -= task.memory_mb


def main() -> None:
    core = GenesisCore()
    core.register("010101", "cuda", 0, enabled=False)
    core.register("local-demo", "cpu", 1024)
    result = core.execute(
        Task(128, ("cpu",)),
        lambda node: {
            "node": node,
            "result": sum(n * n for n in range(10_000)),
        },
    )
    print(result)
    print(core.snapshot())


if __name__ == "__main__":
    main()
