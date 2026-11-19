"""Target and worker contracts shared by every archive source."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Target:
    name: str
    provider: str
    description: str
    interval_hours: float
    metadata: tuple[str, ...]
    manual: bool = False
    capabilities: tuple[str, ...] = ()
    options: dict = field(default_factory=dict)
    lock: str | None = None
    optional_capabilities: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, item):
        target = cls(**{**item, 'metadata': tuple(item['metadata']),
                        'capabilities': tuple(item.get('capabilities', [])),
                        'optional_capabilities': tuple(item.get('optional_capabilities', []))})
        if set(target.capabilities) & set(target.optional_capabilities):
            raise ValueError('a capability cannot be both required and optional')
        if not target.name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in target.name):
            raise ValueError('target names must contain only lowercase letters, digits and hyphens')
        if target.interval_hours <= 0:
            raise ValueError('target interval must be positive')
        for path in target.metadata:
            parts = Path(path).parts
            if Path(path).is_absolute() or '..' in parts or not parts or parts[0] != 'data':
                raise ValueError('target metadata must stay below data/')
        return target


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]
    accepted_codes: tuple[int, ...] = (0,)
    report: str | None = None
    after_failure: bool = False


@dataclass
class Context:
    root: Path
    work: Path
    repository: str
    target: Target
    publish: bool
    options: dict = field(default_factory=dict)
    observations: Path | None = None

    def script(self, filename, *args, **kwargs):
        import sys
        return Step(kwargs.pop('name', filename.removesuffix('.py')),
                    (sys.executable, str(self.root / 'scripts' / filename), *(str(a) for a in args)), **kwargs)

    def task(self, name, *args, **kwargs):
        import sys
        return Step(name, (sys.executable, '-m', 'sonos_firmware.platform.tasks', '--root', str(self.root), name,
                           *(str(a) for a in args)), **kwargs)
