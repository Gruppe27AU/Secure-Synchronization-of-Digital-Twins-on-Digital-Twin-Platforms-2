"""
Unit test for the direction of imports between the packages of admin.

Each package may import its own modules and those of lower layers, never a
higher layer or another package on its own layer. An import cycle between
packages can then not arise, so ``import admin.main`` cannot fail on a
partly initialised module.
"""

import ast
from pathlib import Path

import admin

SOURCE_ROOT = Path(admin.__file__).parent

#: Package prefix and layer, lowest first.
LAYERS = {
    "admin.process": 0,
    "admin.config": 1,
    "admin.sync": 2,
    "admin.services": 2,
    "admin.api": 3,
    "admin.main": 4,
}


def _package(module: str) -> str | None:
    """Return the entry of :data:`LAYERS` that ``module`` belongs to."""
    for prefix in LAYERS:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def _module_name(path: Path) -> str:
    """Return the dotted name of a source file, ``__init__`` dropped."""
    parts = path.relative_to(SOURCE_ROOT.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _admin_imports(path: Path) -> set[str]:
    """Return every ``admin.*`` module the file imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
    return {name for name in imported if name.startswith("admin.")}


def test_every_module_belongs_to_a_layer():
    """Test a new package is placed in a layer before it is used."""
    modules = [_module_name(path) for path in SOURCE_ROOT.rglob("*.py")]

    assert [m for m in modules if m != "admin" and _package(m) is None] == []


def test_imports_point_down_only():
    """Test no package imports a higher layer or a package beside it."""
    wrong = []
    for path in SOURCE_ROOT.rglob("*.py"):
        source = _package(_module_name(path))
        for target in _admin_imports(path):
            target_package = _package(target)
            if target_package == source:
                continue
            if target_package is None or LAYERS[target_package] >= LAYERS[source]:
                wrong.append(f"{_module_name(path)} -> {target}")

    assert wrong == []
