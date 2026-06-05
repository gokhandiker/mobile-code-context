"""
Kotlin source file parser using tree-sitter.

Extracts AST-based structural model from Kotlin files:
- Classes, objects, interfaces (with supertypes)
- Functions and methods
- Properties
- Import statements and package declaration
- Annotations and modifiers
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import tree_sitter_kotlin as tskotlin
from tree_sitter import Language, Parser, Node

logger = structlog.get_logger()

# ── Tree-sitter setup ────────────────────────────────────────────────────────

KOTLIN_LANGUAGE = Language(tskotlin.language())
_parser = Parser(KOTLIN_LANGUAGE)


# ── Data Models ──────────────────────────────────────────────────────────────


class DeclarationKind(str, Enum):
    CLASS = "class"
    OBJECT = "object"
    INTERFACE = "interface"
    DATA_CLASS = "data_class"
    SEALED_CLASS = "sealed_class"
    ENUM_CLASS = "enum_class"
    ANNOTATION_CLASS = "annotation_class"
    COMPANION_OBJECT = "companion_object"
    FUNCTION = "function"
    PROPERTY = "property"


@dataclass
class Span:
    """Start and end line positions (0-indexed internally)."""

    start_line: int  # 0-indexed
    end_line: int  # 0-indexed

    @property
    def start_line_1(self) -> int:
        """1-indexed start line."""
        return self.start_line + 1

    @property
    def end_line_1(self) -> int:
        """1-indexed end line."""
        return self.end_line + 1

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class KotlinDeclaration:
    """A declaration in a Kotlin file."""

    kind: DeclarationKind
    name: str
    span: Span
    annotations: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    superclass: Optional[str] = None
    interfaces: list[str] = field(default_factory=list)
    children: list[KotlinDeclaration] = field(default_factory=list)


@dataclass
class KotlinImport:
    """A single import statement."""

    path: str
    alias: Optional[str] = None
    is_wildcard: bool = False


@dataclass
class KotlinFile:
    """Parsed representation of a Kotlin source file."""

    file_path: str
    package_name: Optional[str] = None
    imports: list[KotlinImport] = field(default_factory=list)
    declarations: list[KotlinDeclaration] = field(default_factory=list)
    source_text: str = ""
    line_count: int = 0

    @property
    def declaration_names(self) -> list[str]:
        """All top-level declaration names."""
        return [d.name for d in self.declarations]

    @property
    def all_declarations_flat(self) -> list[KotlinDeclaration]:
        """Recursively flatten all nested declarations."""
        result: list[KotlinDeclaration] = []
        stack = list(self.declarations)
        while stack:
            decl = stack.pop()
            result.append(decl)
            stack.extend(decl.children)
        return result


# ── Node helpers ─────────────────────────────────────────────────────────────


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _span_from_node(node: Node) -> Span:
    return Span(start_line=node.start_point[0], end_line=node.end_point[0])


def _find_child(node: Node, type_name: str) -> Optional[Node]:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_children(node: Node, type_name: str) -> list[Node]:
    return [c for c in node.children if c.type == type_name]


# ── Extractors ───────────────────────────────────────────────────────────────


def _extract_package(root: Node, source: bytes) -> Optional[str]:
    pkg = _find_child(root, "package_header")
    if pkg is None:
        return None
    qi = _find_child(pkg, "qualified_identifier")
    if qi is not None:
        return _node_text(qi, source)
    ident = _find_child(pkg, "identifier")
    if ident is not None:
        return _node_text(ident, source)
    return None


def _extract_imports(root: Node, source: bytes) -> list[KotlinImport]:
    imports: list[KotlinImport] = []
    for imp_node in _find_children(root, "import"):
        qi = _find_child(imp_node, "qualified_identifier")
        if qi is None:
            continue
        path = _node_text(qi, source)
        full_text = _node_text(imp_node, source)
        is_wildcard = ".*" in full_text
        alias = None
        children = imp_node.children
        for i, child in enumerate(children):
            if child.type == "as" and i + 1 < len(children):
                alias_ident = children[i + 1]
                if alias_ident.type == "identifier":
                    alias = _node_text(alias_ident, source)
        imports.append(KotlinImport(path=path, alias=alias, is_wildcard=is_wildcard))
    return imports


def _extract_annotations(node: Node, source: bytes) -> list[str]:
    annotations: list[str] = []
    modifiers = _find_child(node, "modifiers")
    if modifiers is None:
        return annotations
    for child in modifiers.children:
        if child.type == "annotation":
            ut = _find_child(child, "user_type")
            if ut:
                ident = _find_child(ut, "identifier")
                annotations.append(
                    _node_text(ident, source) if ident else _node_text(ut, source)
                )
            else:
                text = _node_text(child, source).strip().lstrip("@")
                paren = text.find("(")
                if paren > 0:
                    text = text[:paren]
                if text:
                    annotations.append(text)
    return annotations


def _extract_modifiers(node: Node, source: bytes) -> list[str]:
    mods: list[str] = []
    modifiers = _find_child(node, "modifiers")
    if modifiers is None:
        return mods
    for child in modifiers.children:
        if child.type in (
            "visibility_modifier",
            "inheritance_modifier",
            "member_modifier",
            "class_modifier",
        ):
            mods.append(_node_text(child, source))
    return mods


def _has_modifier(node: Node, source: bytes, modifier: str) -> bool:
    modifiers = _find_child(node, "modifiers")
    if modifiers is None:
        return False
    for child in modifiers.children:
        if child.type == "class_modifier" and _node_text(child, source).strip() == modifier:
            return True
    return False


def _is_interface(node: Node) -> bool:
    for child in node.children:
        if child.type == "interface":
            return True
    return False


def _classify_class(node: Node, source: bytes) -> DeclarationKind:
    if _is_interface(node):
        return DeclarationKind.INTERFACE
    if _has_modifier(node, source, "data"):
        return DeclarationKind.DATA_CLASS
    if _has_modifier(node, source, "sealed"):
        return DeclarationKind.SEALED_CLASS
    if _has_modifier(node, source, "enum"):
        return DeclarationKind.ENUM_CLASS
    if _has_modifier(node, source, "annotation"):
        return DeclarationKind.ANNOTATION_CLASS
    return DeclarationKind.CLASS


def _extract_supertypes(node: Node, source: bytes) -> tuple[Optional[str], list[str]]:
    ds = _find_child(node, "delegation_specifiers")
    if ds is None:
        return None, []
    superclass = None
    interfaces: list[str] = []
    for child in _find_children(ds, "delegation_specifier"):
        ci = _find_child(child, "constructor_invocation")
        ut_direct = _find_child(child, "user_type")
        if ci is not None:
            ut = _find_child(ci, "user_type")
            if ut:
                ident = _find_child(ut, "identifier")
                name = _node_text(ident, source) if ident else _node_text(ut, source)
                if superclass is None:
                    superclass = name
                else:
                    interfaces.append(name)
        elif ut_direct is not None:
            ident = _find_child(ut_direct, "identifier")
            name = _node_text(ident, source) if ident else _node_text(ut_direct, source)
            interfaces.append(name)
    return superclass, interfaces


def _extract_declaration(node: Node, source: bytes) -> Optional[KotlinDeclaration]:
    kind: Optional[DeclarationKind] = None
    name: Optional[str] = None

    if node.type == "class_declaration":
        kind = _classify_class(node, source)
        ident = _find_child(node, "identifier")
        if ident:
            name = _node_text(ident, source)
    elif node.type == "object_declaration":
        kind = DeclarationKind.OBJECT
        ident = _find_child(node, "identifier")
        if ident:
            name = _node_text(ident, source)
    elif node.type == "companion_object":
        kind = DeclarationKind.COMPANION_OBJECT
        name = "Companion"
        ident = _find_child(node, "identifier")
        if ident:
            name = _node_text(ident, source)
    elif node.type == "function_declaration":
        kind = DeclarationKind.FUNCTION
        ident = _find_child(node, "identifier")
        if ident:
            name = _node_text(ident, source)
    elif node.type == "property_declaration":
        kind = DeclarationKind.PROPERTY
        var_decl = _find_child(node, "variable_declaration")
        if var_decl:
            ident = _find_child(var_decl, "identifier")
            if ident:
                name = _node_text(ident, source)
    else:
        return None

    if kind is None or name is None:
        return None

    span = _span_from_node(node)
    annotations = _extract_annotations(node, source)
    modifiers = _extract_modifiers(node, source)

    decl = KotlinDeclaration(
        kind=kind,
        name=name,
        span=span,
        annotations=annotations,
        modifiers=modifiers,
    )

    # Supertypes for class-like declarations
    if kind in (
        DeclarationKind.CLASS,
        DeclarationKind.DATA_CLASS,
        DeclarationKind.SEALED_CLASS,
        DeclarationKind.INTERFACE,
        DeclarationKind.OBJECT,
        DeclarationKind.ENUM_CLASS,
    ):
        decl.superclass, decl.interfaces = _extract_supertypes(node, source)

    # Nested declarations
    body = _find_child(node, "class_body") or _find_child(node, "enum_class_body")
    if body:
        for child in body.children:
            nested = _extract_declaration(child, source)
            if nested:
                decl.children.append(nested)

    return decl


# ── Public API ───────────────────────────────────────────────────────────────


def parse_kotlin_source(source_code: str, file_path: str = "<string>") -> KotlinFile:
    """Parse Kotlin source code string into a structured model."""
    source_bytes = source_code.encode("utf-8")
    tree = _parser.parse(source_bytes)
    root = tree.root_node

    declarations: list[KotlinDeclaration] = []
    for child in root.children:
        decl = _extract_declaration(child, source_bytes)
        if decl is not None:
            declarations.append(decl)

    return KotlinFile(
        file_path=file_path,
        package_name=_extract_package(root, source_bytes),
        imports=_extract_imports(root, source_bytes),
        declarations=declarations,
        source_text=source_code,
        line_count=source_code.count("\n") + 1,
    )


def parse_kotlin_file(file_path: Path | str) -> KotlinFile:
    """Parse a Kotlin file from disk."""
    path = Path(file_path)
    source = path.read_text(encoding="utf-8", errors="replace")
    return parse_kotlin_source(source, str(file_path))
