"""
Fan-in analyzer — detects base architecture files by symbol reference frequency.

Files referenced by a high percentage of the codebase are likely core
infrastructure (base classes, extensions, marker interfaces, utilities).

Unlike a naive import counter, this analyzer resolves references at the
*symbol* level (classes, interfaces, objects, top-level/extension functions,
typealiases). This is what lets it catch architectural contracts that are used
everywhere but rarely show up as a plain ``import com.x.SomeClass`` line, e.g.:

- extension functions like ``apiCall`` / ``toFlow`` (imported as ``import pkg.apiCall``)
- marker interfaces used as generic constraints (``UIState``, ``UIAction``)
- transitive base classes (``FlowViewModel`` behind ``StateViewModel``)
- contracts pulled in by the exemplar feature module

The analyzer exposes three complementary signals that the context manager
merges together:

1. ``analyze_fan_in``                — frequency ranking (who is referenced most)
2. ``resolve_exemplar_dependencies`` — the dependency closure of the exemplar
3. ``resolve_supertype_closure``     — transitive supertypes of seed files
"""

from __future__ import annotations

import os
import re
import structlog
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from mobile_code_context.detector.platform import PlatformInfo, IGNORE_DIRS
from mobile_code_context.indexer.parser_kotlin import (
    DeclarationKind,
    KotlinDeclaration,
    parse_kotlin_source,
)

logger = structlog.get_logger()

# Swift is handled with lightweight regex extraction (no tree-sitter round-trip
# needed for the coarse symbol map below). Kotlin uses the real AST parser.
_SWIFT_IMPORT_RE = re.compile(r"^\s*import\s+(\w+)", re.MULTILINE)
_SWIFT_TYPE_RE = re.compile(
    r"^\s*(?:public|internal|open|final|private|fileprivate)?\s*"
    r"(?:class|struct|enum|protocol|extension)\s+(\w+)"
    r"(?:\s*:\s*([\w\s,]+))?",
    re.MULTILINE,
)
_SWIFT_FUNC_RE = re.compile(r"^\s*(?:public|internal|open)?\s*func\s+(\w+)", re.MULTILINE)

# Architectural-contract role labels (used for boosting + relaxed thresholds).
ROLE_BASE_VIEWMODEL = "base_viewmodel"
ROLE_MARKER_INTERFACE = "marker_interface"
ROLE_UI_CONTRACT = "ui_contract"
ROLE_EXTENSION_CONTAINER = "extension_container"
ROLE_SERVICE = "service"

_CONTRACT_ROLES = frozenset(
    {
        ROLE_BASE_VIEWMODEL,
        ROLE_MARKER_INTERFACE,
        ROLE_UI_CONTRACT,
        ROLE_EXTENSION_CONTAINER,
        ROLE_SERVICE,
    }
)


@dataclass
class FanInResult:
    """Result of fan-in analysis for a file."""

    file_path: str
    fan_in_count: int  # How many files reference symbols declared here
    fan_in_ratio: float  # fan_in_count / total_files
    confidence: float  # 0-1 score
    source: str = "fan_in"  # fan_in | exemplar_dep | supertype
    role: Optional[str] = None  # architectural-contract role, if classified


@dataclass
class FileModel:
    """Lightweight structural model of a single source file."""

    rel_path: str
    package: Optional[str]
    symbols: set[str] = field(default_factory=set)  # all top-level declared names
    type_symbols: set[str] = field(default_factory=set)  # class/interface/object names
    supertypes: list[str] = field(default_factory=list)  # referenced super names
    imports: list[tuple[str, bool]] = field(default_factory=list)  # (path, is_wildcard)
    role: Optional[str] = None


@dataclass
class SymbolIndex:
    """Resolved symbol maps for an entire repository.

    Build this once (it walks + parses every file) and reuse it across
    fan-in, exemplar-dependency, and supertype-closure analyses.
    """

    files: dict[str, FileModel] = field(default_factory=dict)
    fqn_to_files: dict[str, list[str]] = field(default_factory=dict)  # pkg.Symbol -> paths
    symbol_to_files: dict[str, list[str]] = field(default_factory=dict)  # Symbol -> paths
    package_files: dict[str, list[str]] = field(default_factory=dict)  # pkg -> paths
    total_files: int = 0


# ── Role classification ──────────────────────────────────────────────────────


def _classify_role(filename: str, decls: list[KotlinDeclaration]) -> Optional[str]:
    """Best-effort architectural-contract role for a Kotlin file.

    Uses structural signals (declaration kind, modifiers, body emptiness) plus
    naming heuristics. Returns ``None`` for ordinary files.
    """
    base = filename
    for ext in (".kt", ".kts"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break

    top_types = [
        d
        for d in decls
        if d.kind
        in (
            DeclarationKind.CLASS,
            DeclarationKind.DATA_CLASS,
            DeclarationKind.SEALED_CLASS,
            DeclarationKind.INTERFACE,
            DeclarationKind.OBJECT,
            DeclarationKind.ENUM_CLASS,
        )
    ]
    top_funcs = [d for d in decls if d.kind == DeclarationKind.FUNCTION]

    # Extension/util container: file is mostly top-level functions, or *Ext naming.
    if (top_funcs and not top_types) or base.endswith(
        ("Ext", "Extensions", "Utils", "Util")
    ):
        return ROLE_EXTENSION_CONTAINER

    for d in top_types:
        mods = {m.strip() for m in d.modifiers}
        name = d.name

        # Base ViewModel: an abstract/open *ViewModel class.
        if name.endswith("ViewModel") and ({"abstract", "open"} & mods):
            return ROLE_BASE_VIEWMODEL

        # Marker interface: interface with an empty body (no members declared).
        if d.kind == DeclarationKind.INTERFACE and not d.children:
            return ROLE_MARKER_INTERFACE

        # UI contract: interface/sealed type whose name encodes an MVI concept.
        if d.kind in (
            DeclarationKind.INTERFACE,
            DeclarationKind.SEALED_CLASS,
        ) and name.endswith(("State", "Effect", "Action", "Event", "Intent")):
            return ROLE_UI_CONTRACT

        # Service contract: scoped/abstract *Service.
        if name.endswith("Service"):
            ann = {a.strip() for a in d.annotations}
            if ({"abstract", "open"} & mods) or (
                ann & {"Singleton", "ActivityRetainedScoped", "ViewModelScoped"}
            ):
                return ROLE_SERVICE

    return None


# ── Symbol index construction ────────────────────────────────────────────────


def _model_from_kotlin(rel_path: str, filename: str, content: str) -> FileModel:
    parsed = parse_kotlin_source(content, rel_path)
    symbols: set[str] = set()
    type_symbols: set[str] = set()
    supertypes: list[str] = []

    for decl in parsed.declarations:
        symbols.add(decl.name)
        if decl.kind in (
            DeclarationKind.CLASS,
            DeclarationKind.DATA_CLASS,
            DeclarationKind.SEALED_CLASS,
            DeclarationKind.INTERFACE,
            DeclarationKind.OBJECT,
            DeclarationKind.ENUM_CLASS,
            DeclarationKind.ANNOTATION_CLASS,
        ):
            type_symbols.add(decl.name)
            if decl.superclass:
                supertypes.append(decl.superclass)
            supertypes.extend(decl.interfaces)

    return FileModel(
        rel_path=rel_path,
        package=parsed.package_name,
        symbols=symbols,
        type_symbols=type_symbols,
        supertypes=supertypes,
        imports=[(imp.path, imp.is_wildcard) for imp in parsed.imports],
        role=_classify_role(filename, parsed.declarations),
    )


def _model_from_swift(rel_path: str, content: str) -> FileModel:
    symbols: set[str] = set()
    type_symbols: set[str] = set()
    supertypes: list[str] = []

    for name, inherits in _SWIFT_TYPE_RE.findall(content):
        symbols.add(name)
        type_symbols.add(name)
        if inherits:
            for parent in inherits.split(","):
                parent = parent.strip()
                if parent:
                    supertypes.append(parent)

    for name in _SWIFT_FUNC_RE.findall(content):
        symbols.add(name)

    imports = [(m, False) for m in _SWIFT_IMPORT_RE.findall(content)]

    return FileModel(
        rel_path=rel_path,
        package=None,  # Swift modules are not package-qualified per-file
        symbols=symbols,
        type_symbols=type_symbols,
        supertypes=supertypes,
        imports=imports,
        role=None,
    )


def build_symbol_index(repo_path: Path, platform: PlatformInfo) -> SymbolIndex:
    """Walk + parse the repository into a reusable :class:`SymbolIndex`."""
    extensions = set(platform.extensions)
    index = SymbolIndex()

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for filename in files:
            _, ext = os.path.splitext(filename)
            if ext not in extensions:
                continue

            abs_path = Path(root) / filename
            rel_path = str(abs_path.relative_to(repo_path))
            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            if ext in (".kt", ".kts"):
                try:
                    model = _model_from_kotlin(rel_path, filename, content)
                except Exception:  # pragma: no cover - parser robustness
                    logger.debug("kotlin_parse_failed", file=rel_path)
                    continue
            elif ext == ".swift":
                model = _model_from_swift(rel_path, content)
            else:
                continue

            index.files[rel_path] = model

            if model.package:
                index.package_files.setdefault(model.package, []).append(rel_path)

            for sym in model.symbols:
                index.symbol_to_files.setdefault(sym, []).append(rel_path)
                if model.package:
                    index.fqn_to_files.setdefault(
                        f"{model.package}.{sym}", []
                    ).append(rel_path)

    index.total_files = len(index.files)
    logger.info("symbol_index_built", total_files=index.total_files)
    return index


# ── Reference resolution ─────────────────────────────────────────────────────


def _resolve_import(index: SymbolIndex, path: str, is_wildcard: bool) -> list[str]:
    """Resolve an import statement to the repo file(s) it points at."""
    if is_wildcard:
        # `import pkg.*` — credit only contract-role files in that package to
        # avoid flooding fan-in with every same-package symbol.
        return [
            p
            for p in index.package_files.get(path, [])
            if index.files[p].role in _CONTRACT_ROLES
        ]

    files = index.fqn_to_files.get(path)
    if files:
        return files

    # Fallback: resolve by the trailing symbol name (covers cross-package
    # extension functions / re-exports the FQN map may have missed).
    seg = path.rsplit(".", 1)[-1]
    return index.symbol_to_files.get(seg, [])


def _resolve_symbol(
    index: SymbolIndex, name: str, exclude: Optional[str] = None
) -> list[str]:
    """Resolve a bare symbol name (e.g. a supertype) to declaring file(s)."""
    # Strip generic args: `StateViewModel<S, A>` -> `StateViewModel`.
    name = name.split("<", 1)[0].strip()
    name = name.rsplit(".", 1)[-1]
    return [p for p in index.symbol_to_files.get(name, []) if p != exclude]


# ── Public analyses ──────────────────────────────────────────────────────────


def analyze_fan_in(
    repo_path: Path,
    platform: PlatformInfo,
    max_results: int = 15,
    min_ratio: float = 0.02,
    contract_min_ratio: float = 0.005,
    index: Optional[SymbolIndex] = None,
) -> list[FanInResult]:
    """Rank files by how many other files reference their declared symbols.

    Args:
        repo_path: Repository root path
        platform: Detected platform info
        max_results: Maximum number of base files to return
        min_ratio: Minimum fan-in ratio for ordinary files
        contract_min_ratio: Relaxed ratio for architectural-contract roles
        index: Pre-built symbol index (built internally if omitted)

    Returns:
        Sorted list of FanInResult (highest fan-in first)
    """
    if index is None:
        index = build_symbol_index(repo_path, platform)

    total_files = index.total_files
    if total_files == 0:
        return []

    # Count references: each importing file contributes at most once per target.
    import_counts: dict[str, int] = {}
    for model in index.files.values():
        credited: set[str] = set()
        for path, is_wildcard in model.imports:
            for target in _resolve_import(index, path, is_wildcard):
                if target == model.rel_path or target in credited:
                    continue
                credited.add(target)
                import_counts[target] = import_counts.get(target, 0) + 1

    # Rank, applying a relaxed threshold + confidence boost to contract roles.
    results: list[FanInResult] = []
    for file_path, count in import_counts.items():
        ratio = count / total_files
        role = index.files[file_path].role
        threshold = contract_min_ratio if role in _CONTRACT_ROLES else min_ratio
        if ratio < threshold:
            continue
        confidence = min(ratio * 10, 1.0)
        if role in _CONTRACT_ROLES:
            confidence = min(confidence + 0.3, 1.0)
        results.append(
            FanInResult(
                file_path=file_path,
                fan_in_count=count,
                fan_in_ratio=ratio,
                confidence=confidence,
                source="fan_in",
                role=role,
            )
        )

    # Sort: contracts first, then by fan-in count.
    results.sort(
        key=lambda r: (r.role in _CONTRACT_ROLES, r.fan_in_count),
        reverse=True,
    )

    # Prefer files in core/base/shared/common paths among equal-rank entries.
    prioritized: list[FanInResult] = []
    others: list[FanInResult] = []
    for r in results:
        lower = r.file_path.lower()
        if any(
            seg in lower
            for seg in ("core/", "base/", "shared/", "common/", "foundation/")
        ):
            prioritized.append(r)
        else:
            others.append(r)

    final = (prioritized + others)[:max_results]

    logger.info(
        "fan_in_analysis_complete",
        total_files=total_files,
        candidates=len(results),
        selected=len(final),
    )
    return final


def resolve_exemplar_dependencies(
    index: SymbolIndex,
    exemplar_files: list[str],
    max_results: int = 12,
    depth: int = 2,
) -> list[FanInResult]:
    """Resolve the dependency closure of the exemplar feature module.

    Walks the exemplar's imports and supertypes, resolving each to the repo
    file that declares it. These are the architectural contracts a new feature
    must conform to — exactly the files plain fan-in tends to miss.

    The exemplar files themselves are expanded fully (level 0). Discovered
    files are only expanded further when they are architectural *contracts*
    (e.g. a scoped service that itself emits a sealed ``ViewEffect``), which
    keeps the closure tight while still reaching second-order contracts.
    """
    seen: set[str] = set(exemplar_files)
    results: list[FanInResult] = []
    frontier = list(exemplar_files)

    def _targets_of(model: FileModel) -> set[str]:
        targets: set[str] = set()
        for path, is_wildcard in model.imports:
            targets.update(_resolve_import(index, path, is_wildcard))
        for supertype in model.supertypes:
            targets.update(_resolve_symbol(index, supertype, exclude=model.rel_path))
        # Contracts often collaborate with same-package sibling contracts that
        # they reference without an import (e.g. a service emitting a sealed
        # ``ViewEffect`` declared next to it). Pull those cohesive siblings in.
        if model.role in _CONTRACT_ROLES and model.package:
            for sibling in index.package_files.get(model.package, []):
                if sibling != model.rel_path and index.files[sibling].role in _CONTRACT_ROLES:
                    targets.add(sibling)
        return targets

    for level in range(max(depth, 1)):
        next_frontier: list[str] = []
        for rel_path in frontier:
            model = index.files.get(rel_path)
            if model is None:
                continue
            # Beyond the exemplar itself, only contracts get re-expanded.
            if level > 0 and model.role not in _CONTRACT_ROLES:
                continue
            for target in _targets_of(model):
                if target in seen:
                    continue
                seen.add(target)
                next_frontier.append(target)
                results.append(
                    FanInResult(
                        file_path=target,
                        fan_in_count=0,
                        fan_in_ratio=0.0,
                        confidence=0.9,
                        source="exemplar_dep",
                        role=index.files[target].role,
                    )
                )
                if len(results) >= max_results:
                    return results
        if not next_frontier:
            break
        frontier = next_frontier

    return results


def resolve_supertype_closure(
    index: SymbolIndex,
    seed_paths: list[str],
    depth: int = 2,
    max_results: int = 12,
) -> list[FanInResult]:
    """Pull transitive supertypes of the seed files into the closure.

    Catches contracts that sit *above* a directly-referenced base class, e.g.
    ``FlowViewModel`` when only ``StateViewModel`` is referenced by features.
    """
    seen: set[str] = set(seed_paths)
    results: list[FanInResult] = []
    frontier = list(seed_paths)

    for _ in range(max(depth, 0)):
        next_frontier: list[str] = []
        for rel_path in frontier:
            model = index.files.get(rel_path)
            if model is None:
                continue
            for supertype in model.supertypes:
                for target in _resolve_symbol(index, supertype, exclude=rel_path):
                    if target in seen:
                        continue
                    seen.add(target)
                    next_frontier.append(target)
                    results.append(
                        FanInResult(
                            file_path=target,
                            fan_in_count=0,
                            fan_in_ratio=0.0,
                            confidence=0.85,
                            source="supertype",
                            role=index.files[target].role,
                        )
                    )
                    if len(results) >= max_results:
                        return results
        if not next_frontier:
            break
        frontier = next_frontier

    return results
