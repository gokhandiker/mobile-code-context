"""Tests for fan-in analysis."""

import pytest
from pathlib import Path

from mobile_code_context.context.fan_in import (
    analyze_fan_in,
    build_symbol_index,
    resolve_exemplar_dependencies,
    resolve_supertype_closure,
)
from mobile_code_context.detector.platform import PlatformInfo, PlatformType, Language


def _android_platform() -> PlatformInfo:
    return PlatformInfo(
        type=PlatformType.ANDROID,
        languages=[Language.KOTLIN],
        extensions=[".kt", ".kts"],
    )



def _create_kotlin_project(tmp_path: Path) -> None:
    """Create a minimal Kotlin project structure for testing."""
    # Base class (should have high fan-in)
    base_dir = tmp_path / "core" / "base" / "src" / "main" / "kotlin"
    base_dir.mkdir(parents=True)
    (base_dir / "BaseViewModel.kt").write_text(
        "package com.example.core.base\n\nabstract class BaseViewModel {}\n"
    )
    (base_dir / "BaseUseCase.kt").write_text(
        "package com.example.core.base\n\nabstract class BaseUseCase {}\n"
    )

    # Feature files that import base classes
    for i in range(10):
        feat_dir = tmp_path / f"features/feat{i}/src/main/kotlin"
        feat_dir.mkdir(parents=True)
        (feat_dir / f"Feat{i}ViewModel.kt").write_text(
            f"package com.example.features.feat{i}\n\n"
            f"import com.example.core.base.BaseViewModel\n\n"
            f"class Feat{i}ViewModel : BaseViewModel() {{}}\n"
        )


def test_fan_in_detects_base_classes(tmp_path: Path):
    """Fan-in analysis should detect frequently imported base classes."""
    _create_kotlin_project(tmp_path)

    platform = PlatformInfo(
        type=PlatformType.ANDROID,
        languages=[Language.KOTLIN],
        extensions=[".kt", ".kts"],
    )

    results = analyze_fan_in(tmp_path, platform, max_results=5, min_ratio=0.01)

    # BaseViewModel should be found (imported by 10 files)
    assert len(results) > 0
    file_paths = [r.file_path for r in results]
    assert any("BaseViewModel" in p for p in file_paths)


def test_fan_in_empty_project(tmp_path: Path):
    """Empty project should return no results."""
    platform = PlatformInfo(
        type=PlatformType.ANDROID,
        languages=[Language.KOTLIN],
        extensions=[".kt"],
    )

    results = analyze_fan_in(tmp_path, platform)

    assert results == []


# ── Symbol-aware detection (extension fns, markers, supertypes, exemplar) ─────


def _create_boyner_like_project(tmp_path: Path) -> None:
    """A fixture mirroring the Boyner architecture contracts that plain
    import-counting misses: extension functions, marker interfaces, and a
    transitive base-class chain (StateViewModel -> FlowViewModel)."""
    core = tmp_path / "core" / "src" / "main" / "kotlin" / "com" / "example" / "core"

    # MVI marker interfaces (empty bodies -> marker_interface role)
    mvi = core / "mvi"
    mvi.mkdir(parents=True)
    (mvi / "UIState.kt").write_text(
        "package com.example.core.mvi\n\ninterface UIState\n"
    )
    (mvi / "UIAction.kt").write_text(
        "package com.example.core.mvi\n\ninterface UIAction\n"
    )
    (mvi / "ViewEffect.kt").write_text(
        "package com.example.core.mvi\n\nsealed interface ViewEffect\n"
    )

    # Base ViewModel chain: StateViewModel extends FlowViewModel
    base = core / "base"
    base.mkdir(parents=True)
    (base / "FlowViewModel.kt").write_text(
        "package com.example.core.base\n\n"
        "abstract class FlowViewModel {\n"
        "    fun sendRequest() {}\n"
        "}\n"
    )
    (base / "StateViewModel.kt").write_text(
        "package com.example.core.base\n\n"
        "import com.example.core.base.FlowViewModel\n"
        "import com.example.core.mvi.UIState\n"
        "import com.example.core.mvi.UIAction\n\n"
        "abstract class StateViewModel<S : UIState, A : UIAction> : FlowViewModel() {\n"
        "    fun setState(s: S) {}\n"
        "}\n"
    )

    # Extension containers (top-level functions -> extension_container role)
    ext = core / "ext"
    ext.mkdir(parents=True)
    (ext / "UseCaseExt.kt").write_text(
        "package com.example.core.ext\n\n"
        "fun apiCall(): Unit = Unit\n"
    )
    (ext / "RepositoryExt.kt").write_text(
        "package com.example.core.ext\n\n"
        "fun toFlow(): Unit = Unit\n"
    )

    # Scoped service contract
    nav = core / "nav"
    nav.mkdir(parents=True)
    (nav / "NavigationAndEffectService.kt").write_text(
        "package com.example.core.nav\n\n"
        "@ActivityRetainedScoped\n"
        "class NavigationAndEffectService {\n"
        "    fun emit() {}\n"
        "}\n"
    )

    # Many features that reference contracts WITHOUT importing every base file:
    # they import the extension functions + marker interfaces + StateViewModel.
    for i in range(12):
        feat = tmp_path / "features" / f"feat{i}" / "src" / "main" / "kotlin"
        feat.mkdir(parents=True)
        (feat / f"Feat{i}State.kt").write_text(
            f"package com.example.features.feat{i}\n\n"
            f"import com.example.core.mvi.UIState\n\n"
            f"data class Feat{i}State(val x: Int) : UIState\n"
        )
        (feat / f"Feat{i}Action.kt").write_text(
            f"package com.example.features.feat{i}\n\n"
            f"import com.example.core.mvi.UIAction\n\n"
            f"sealed class Feat{i}Action : UIAction\n"
        )
        (feat / f"Feat{i}ViewModel.kt").write_text(
            f"package com.example.features.feat{i}\n\n"
            f"import com.example.core.base.StateViewModel\n"
            f"import com.example.core.ext.apiCall\n"
            f"import com.example.core.ext.toFlow\n\n"
            f"class Feat{i}ViewModel : "
            f"StateViewModel<Feat{i}State, Feat{i}Action>() {{\n"
            f"    fun load() {{ apiCall(); toFlow() }}\n"
            f"}}\n"
        )


def test_extension_function_is_detected(tmp_path: Path):
    """Top-level/extension functions imported by name must be resolved."""
    _create_boyner_like_project(tmp_path)
    results = analyze_fan_in(tmp_path, _android_platform(), max_results=30, min_ratio=0.01)
    paths = [r.file_path for r in results]
    assert any("UseCaseExt.kt" in p for p in paths), paths
    assert any("RepositoryExt.kt" in p for p in paths), paths


def test_marker_interface_passes_relaxed_threshold(tmp_path: Path):
    """Marker interfaces should survive even when the normal ratio excludes them."""
    _create_boyner_like_project(tmp_path)
    # min_ratio so high that ordinary files are excluded; contracts use the
    # relaxed contract_min_ratio and should still appear.
    results = analyze_fan_in(
        tmp_path,
        _android_platform(),
        max_results=30,
        min_ratio=0.9,
        contract_min_ratio=0.01,
    )
    paths = [r.file_path for r in results]
    roles = {r.file_path: r.role for r in results}
    assert any("UIState.kt" in p for p in paths), paths
    assert any("UIAction.kt" in p for p in paths), paths
    assert any(roles[p] == "marker_interface" for p in paths if "UIState.kt" in p)


def test_supertype_closure_finds_transitive_base(tmp_path: Path):
    """FlowViewModel is only referenced via StateViewModel -> must be pulled in."""
    _create_boyner_like_project(tmp_path)
    platform = _android_platform()
    index = build_symbol_index(tmp_path, platform)

    # Seed with StateViewModel only.
    seed = [p for p in index.files if "StateViewModel.kt" in p]
    assert seed, "fixture should declare StateViewModel"

    closure = resolve_supertype_closure(index, seed, depth=2)
    paths = [r.file_path for r in closure]
    assert any("FlowViewModel.kt" in p for p in paths), paths
    assert all(r.source == "supertype" for r in closure)


def test_exemplar_dependencies_resolves_contracts(tmp_path: Path):
    """The exemplar's imports + supertypes resolve to base contracts."""
    _create_boyner_like_project(tmp_path)
    platform = _android_platform()
    index = build_symbol_index(tmp_path, platform)

    exemplar_files = [
        p
        for p in index.files
        if "feat0" in p and p.endswith(("ViewModel.kt", "State.kt", "Action.kt"))
    ]
    assert exemplar_files

    deps = resolve_exemplar_dependencies(index, exemplar_files, max_results=20)
    paths = [r.file_path for r in deps]
    assert any("StateViewModel.kt" in p for p in paths), paths
    assert any("UseCaseExt.kt" in p for p in paths), paths
    assert any("UIState.kt" in p for p in paths), paths
    assert all(r.source == "exemplar_dep" for r in deps)


def test_role_classification(tmp_path: Path):
    """Contract roles should be assigned for the architectural files."""
    _create_boyner_like_project(tmp_path)
    index = build_symbol_index(tmp_path, _android_platform())

    def role_of(suffix: str):
        for path, model in index.files.items():
            if path.endswith(suffix):
                return model.role
        return None

    assert role_of("StateViewModel.kt") == "base_viewmodel"
    assert role_of("UIState.kt") == "marker_interface"
    assert role_of("UseCaseExt.kt") == "extension_container"
    assert role_of("NavigationAndEffectService.kt") == "service"

