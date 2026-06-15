"""Tests for the token/quality optimization features (all project-agnostic)."""

from pathlib import Path

from mobile_code_context.config import Settings
from mobile_code_context.context.fan_in import FileModel, SymbolIndex
from mobile_code_context.context.manager import _dedup_parallel_base_files
from mobile_code_context.context.vertical_slice import find_best_exemplar
from mobile_code_context.detector.platform import (
    Language,
    ModuleDetection,
    PlatformInfo,
    PlatformType,
)
from mobile_code_context.reindex.delta import DeltaReindexer


def _android_platform() -> PlatformInfo:
    return PlatformInfo(
        type=PlatformType.ANDROID,
        languages=[Language.KOTLIN],
        extensions=[".kt", ".kts"],
    )


def _android_platform_with(modules) -> PlatformInfo:
    return PlatformInfo(
        type=PlatformType.ANDROID,
        languages=[Language.KOTLIN],
        extensions=[".kt", ".kts"],
        modules=modules,
    )


# ── Config prefix parsing ────────────────────────────────────────────────────


def test_config_prefix_lists_parse(tmp_path: Path):
    s = Settings(
        repo_path=tmp_path,
        exclude_module_prefixes="legacy/, /old ,",
        include_module_prefixes="apps/main",
    )
    assert s.exclude_module_prefixes_list == ["legacy", "old"]
    assert s.include_module_prefixes_list == ["apps/main"]


def test_config_prefix_lists_default_empty(tmp_path: Path):
    s = Settings(repo_path=tmp_path)
    assert s.exclude_module_prefixes_list == []
    assert s.include_module_prefixes_list == []


# ── Delta scope (include/exclude) ────────────────────────────────────────────


def test_delta_is_in_scope_exclude(tmp_path: Path):
    s = Settings(repo_path=tmp_path, exclude_module_prefixes="legacy")
    r = DeltaReindexer(s, _android_platform(), embedder=None, store=None)
    assert r._is_in_scope("apps/main/Foo.kt") is True
    assert r._is_in_scope("legacy/Foo.kt") is False
    assert r._is_in_scope("legacyish/Foo.kt") is True  # prefix-boundary aware


def test_delta_is_in_scope_include_allowlist(tmp_path: Path):
    s = Settings(repo_path=tmp_path, include_module_prefixes="apps/main")
    r = DeltaReindexer(s, _android_platform(), embedder=None, store=None)
    assert r._is_in_scope("apps/main/Foo.kt") is True
    assert r._is_in_scope("apps/other/Foo.kt") is False


# ── Mandatory dedup ──────────────────────────────────────────────────────────


def _index_with(symbols: dict[str, list[str]]) -> SymbolIndex:
    idx = SymbolIndex()
    for path, types in symbols.items():
        idx.files[path] = FileModel(rel_path=path, package=None, type_symbols=set(types))
    return idx


def test_mandatory_dedup_collapses_parallel_symbol():
    base_files = [
        {"file_path": "appA/Base.kt", "fan_in": 5, "confidence": 0.5},
        {"file_path": "appB/Base.kt", "fan_in": 9, "confidence": 0.9},
    ]
    index = _index_with({"appA/Base.kt": ["Base"], "appB/Base.kt": ["Base"]})
    out = _dedup_parallel_base_files(base_files, index)
    assert len(out) == 1
    # Highest fan-in survives by default.
    assert out[0]["file_path"] == "appB/Base.kt"


def test_mandatory_dedup_respects_preferred_prefix():
    base_files = [
        {"file_path": "appA/Base.kt", "fan_in": 9, "confidence": 0.9},
        {"file_path": "appB/Base.kt", "fan_in": 1, "confidence": 0.1},
    ]
    index = _index_with({"appA/Base.kt": ["Base"], "appB/Base.kt": ["Base"]})
    out = _dedup_parallel_base_files(base_files, index, preferred_prefix="appB")
    assert len(out) == 1
    assert out[0]["file_path"] == "appB/Base.kt"


def test_mandatory_dedup_keeps_distinct_and_single_root():
    base_files = [
        {"file_path": "appA/Base.kt", "fan_in": 5, "confidence": 0.5},
        {"file_path": "appA/Other.kt", "fan_in": 5, "confidence": 0.5},
    ]
    index = _index_with({"appA/Base.kt": ["Base"], "appA/Other.kt": ["Other"]})
    out = _dedup_parallel_base_files(base_files, index)
    assert len(out) == 2


# ── Exemplar override ────────────────────────────────────────────────────────


def _make_feature(tmp_path: Path, module: str, extra: int = 0) -> None:
    d = tmp_path / module / "src"
    d.mkdir(parents=True)
    (d / "FooViewModel.kt").write_text("class FooViewModel\n")
    (d / "FooScreen.kt").write_text("class FooScreen\n")
    (d / "FooUseCase.kt").write_text("class FooUseCase\n")
    for i in range(extra):
        (d / f"Helper{i}.kt").write_text(f"class Helper{i}\n")


def _module(path: str) -> ModuleDetection:
    return ModuleDetection(name=path.split("/")[-1], path=path, build_file="", is_feature=True)


def test_exemplar_override_pins_module(tmp_path: Path):
    _make_feature(tmp_path, "modules/alpha")
    _make_feature(tmp_path, "modules/beta")
    platform = _android_platform_with([_module("modules/alpha"), _module("modules/beta")])
    result = find_best_exemplar(tmp_path, platform, preferred_module="modules/beta")
    assert result is not None
    assert result.module_path == "modules/beta"


def test_exemplar_override_falls_back_when_unresolved(tmp_path: Path):
    _make_feature(tmp_path, "modules/alpha")
    platform = _android_platform_with([_module("modules/alpha")])
    # Non-existent override → falls back to auto-selection (alpha exists).
    result = find_best_exemplar(tmp_path, platform, preferred_module="does/not/exist")
    assert result is not None
    assert result.module_path == "modules/alpha"


def test_exemplar_auto_prefers_richer_module(tmp_path: Path):
    """Among equally-complete modules, the richer (more files) one is chosen."""
    _make_feature(tmp_path, "modules/small")
    _make_feature(tmp_path, "modules/big", extra=5)
    platform = _android_platform_with([_module("modules/small"), _module("modules/big")])
    result = find_best_exemplar(tmp_path, platform)
    assert result is not None
    assert result.module_path == "modules/big"

