"""
Swift source file parser using tree-sitter.

Extracts AST-based structural model from Swift files:
- Classes, structs, enums, protocols, extensions
- Functions and methods
- Properties
- Import statements
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import tree_sitter_swift as tsswift
from tree_sitter import Language, Parser, Node

logger = structlog.get_logger()

# ── Tree-sitter setup ────────────────────────────────────────────────────────

SWIFT_LANGUAGE = Language(tsswift.language())
_parser = Parser(SWIFT_LANGUAGE)


# ── Data Models ──────────────────────────────────────────────────────────────


class SwiftDeclarationKind(str, Enum):
    CLASS = "class"
    STRUCT = "struct"
    ENUM = "enum"
    PROTOCOL = "protocol"
    EXTENSION = "extension"
    FUNCTION = "function"
    PROPERTY = "property"
    TYPEALIAS = "typealias"


@dataclass
class Span:
    start_line: int  # 0-indexed
    end_line: int  # 0-indexed

    @property
    def start_line_1(self) -> int:
        return self.start_line + 1

    @property
    def end_line_1(self) -> int:
        return self.end_line + 1

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class SwiftDeclaration:
    kind: SwiftDeclarationKind
    name: str
    span: Span
    modifiers: list[str] = field(default_factory=list)
    superclass: Optional[str] = None
    protocols: list[str] = field(default_factory=list)
    children: list[SwiftDeclaration] = field(default_factory=list)


@dataclass
class SwiftImport:
    module: str


@dataclass
class SwiftFile:
    file_path: str
    imports: list[SwiftImport] = field(default_factory=list)
    declarations: list[SwiftDeclaration] = field(default_factory=list)
    source_text: str = ""
    line_count: int = 0

    @property
    def declaration_names(self) -> list[str]:
        return [d.name for d in self.declarations]

    @property
    def all_declarations_flat(self) -> list[SwiftDeclaration]:
        result: list[SwiftDeclaration] = []
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


def _extract_imports(root: Node, source: bytes) -> list[SwiftImport]:
    imports: list[SwiftImport] = []
    for child in root.children:
        if child.type == "import_declaration":
            # import_declaration → 'import' identifier
            text = _node_text(child, source)
            parts = text.split()
            if len(parts) >= 2:
                module = parts[-1]  # Last part is the module name
                imports.append(SwiftImport(module=module))
    return imports


def _extract_modifiers(node: Node, source: bytes) -> list[str]:
    mods: list[str] = []
    for child in node.children:
        if child.type in ("modifiers", "modifier"):
            mods.append(_node_text(child, source).strip())
        elif child.type == "attribute":
            mods.append(_node_text(child, source).strip())
    return mods


def _extract_declaration(node: Node, source: bytes) -> Optional[SwiftDeclaration]:
    kind: Optional[SwiftDeclarationKind] = None
    name: Optional[str] = None

    if node.type == "class_declaration":
        kind = SwiftDeclarationKind.CLASS
        name_node = _find_child(node, "type_identifier") or _find_child(node, "identifier")
        if name_node:
            name = _node_text(name_node, source)
    elif node.type == "struct_declaration":
        kind = SwiftDeclarationKind.STRUCT
        name_node = _find_child(node, "type_identifier") or _find_child(node, "identifier")
        if name_node:
            name = _node_text(name_node, source)
    elif node.type == "enum_declaration":
        kind = SwiftDeclarationKind.ENUM
        name_node = _find_child(node, "type_identifier") or _find_child(node, "identifier")
        if name_node:
            name = _node_text(name_node, source)
    elif node.type == "protocol_declaration":
        kind = SwiftDeclarationKind.PROTOCOL
        name_node = _find_child(node, "type_identifier") or _find_child(node, "identifier")
        if name_node:
            name = _node_text(name_node, source)
    elif node.type == "extension_declaration":
        kind = SwiftDeclarationKind.EXTENSION
        name_node = _find_child(node, "type_identifier") or _find_child(node, "identifier")
        if name_node:
            name = _node_text(name_node, source)
        else:
            name = "Extension"
    elif node.type == "function_declaration":
        kind = SwiftDeclarationKind.FUNCTION
        name_node = _find_child(node, "simple_identifier") or _find_child(node, "identifier")
        if name_node:
            name = _node_text(name_node, source)
    elif node.type in ("property_declaration", "variable_declaration"):
        kind = SwiftDeclarationKind.PROPERTY
        # Look for pattern binding
        pattern = _find_child(node, "pattern")
        if pattern:
            ident = _find_child(pattern, "simple_identifier")
            if ident:
                name = _node_text(ident, source)
        if name is None:
            ident = _find_child(node, "simple_identifier") or _find_child(node, "identifier")
            if ident:
                name = _node_text(ident, source)
    elif node.type == "typealias_declaration":
        kind = SwiftDeclarationKind.TYPEALIAS
        name_node = _find_child(node, "type_identifier") or _find_child(node, "identifier")
        if name_node:
            name = _node_text(name_node, source)
    else:
        return None

    if kind is None or name is None:
        return None

    span = _span_from_node(node)
    modifiers = _extract_modifiers(node, source)

    decl = SwiftDeclaration(
        kind=kind,
        name=name,
        span=span,
        modifiers=modifiers,
    )

    # Extract nested declarations from class body
    body = _find_child(node, "class_body") or _find_child(node, "enum_body")
    if body:
        for child in body.children:
            nested = _extract_declaration(child, source)
            if nested:
                decl.children.append(nested)

    return decl


# ── Public API ───────────────────────────────────────────────────────────────


def parse_swift_source(source_code: str, file_path: str = "<string>") -> SwiftFile:
    """Parse Swift source code string into a structured model."""
    source_bytes = source_code.encode("utf-8")
    tree = _parser.parse(source_bytes)
    root = tree.root_node

    declarations: list[SwiftDeclaration] = []
    for child in root.children:
        decl = _extract_declaration(child, source_bytes)
        if decl is not None:
            declarations.append(decl)

    return SwiftFile(
        file_path=file_path,
        imports=_extract_imports(root, source_bytes),
        declarations=declarations,
        source_text=source_code,
        line_count=source_code.count("\n") + 1,
    )


def parse_swift_file(file_path: Path | str) -> SwiftFile:
    """Parse a Swift file from disk."""
    path = Path(file_path)
    source = path.read_text(encoding="utf-8", errors="replace")
    return parse_swift_source(source, str(file_path))
