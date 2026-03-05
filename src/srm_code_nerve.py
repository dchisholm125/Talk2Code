import ast
import os
from pathlib import Path
from typing import Dict, List, Set, Any, Optional

class ASTGraphBuilder:
    def __init__(self, root_path: str):
        self.root_path = Path(root_path).resolve()
        # nodes[id] = { "type": "class|function|variable", "file": "...", "line": 123 }
        self.nodes: Dict[str, Dict[str, Any]] = {}
        # edges = { (from_id, to_id, type) }
        self.edges: Set[tuple[str, str, str]] = set()

    def build(self):
        """Finds all .py files and parses them into a dependency graph."""
        py_files = list(self.root_path.rglob("*.py"))
        # Exclude common boilerplate/virtualenv dirs
        py_files = [
            f for f in py_files 
            if not any(p in f.parts for p in [".venv", "venv", "__pycache__", ".git"])
        ]

        # Phase 1: Identity all symbols (Classes, Functions, Variables)
        for f in py_files:
            try:
                rel = self._get_rel_path(f)
                tree = ast.parse(f.read_text(encoding='utf-8'))
                self._extract_symbols(tree, rel)
            except Exception as e:
                print(f"Warning: Failed to extract symbols from {f}: {e}")

        # Phase 2: Map relationships (CALLS, IMPORTS)
        for f in py_files:
            try:
                rel = self._get_rel_path(f)
                tree = ast.parse(f.read_text(encoding='utf-8'))
                self._extract_dependencies(tree, rel)
            except Exception as e:
                print(f"Warning: Failed to extract dependencies from {f}: {e}")

    def _get_rel_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root_path))
        except ValueError:
            return str(path)

    def update_file(self, filepath: str):
        """Incrementally updates symbols and dependencies for a single file."""
        f = Path(filepath).resolve()
        rel = self._get_rel_path(f)
        
        # 1. Remove stale nodes
        to_remove = [node_id for node_id, info in self.nodes.items() if info.get('file') == rel]
        for nid in to_remove:
            del self.nodes[nid]
            
        # 2. Remove stale edges where source is in this file
        # We use a list to avoid 'set changed size during iteration'
        stale_edges = [edge for edge in self.edges if edge[0].startswith(rel)]
        for edge in stale_edges:
            self.edges.remove(edge)

        # 3. Parse and extract fresh data
        if f.exists():
            try:
                tree = ast.parse(f.read_text(encoding='utf-8'))
                self._extract_symbols(tree, rel)
                self._extract_dependencies(tree, rel)
                print(f"Incremental update: Handled {len(to_remove)} removed nodes from {rel}")
            except Exception as e:
                print(f"Error updating file {rel}: {e}")

    def _extract_symbols(self, tree: ast.AST, rel_path: str):
        class SymbolVisitor(ast.NodeVisitor):
            def __init__(self, rel_path_val: str, nodes_dict: Dict):
                self.rel_path = rel_path_val
                self.nodes = nodes_dict
                self.scope_stack = []

            def visit_ClassDef(self, node):
                self.scope_stack.append(node.name)
                full_id = f"{self.rel_path}::{'::'.join(self.scope_stack)}"
                self.nodes[full_id] = {"type": "class", "file": self.rel_path, "line": node.lineno}
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_FunctionDef(self, node):
                self.scope_stack.append(node.name)
                full_id = f"{self.rel_path}::{'::'.join(self.scope_stack)}"
                self.nodes[full_id] = {"type": "function", "file": self.rel_path, "line": node.lineno}
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_AsyncFunctionDef(self, node):
                self.visit_FunctionDef(node)

            def visit_Assign(self, node):
                # Only track global variables at the top level of the file
                if not self.scope_stack:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            full_id = f"{self.rel_path}::{target.id}"
                            self.nodes[full_id] = {"type": "variable", "file": self.rel_path, "line": node.lineno}
                self.generic_visit(node)

        SymbolVisitor(rel_path, self.nodes).visit(tree)

    def _extract_dependencies(self, tree: ast.AST, rel_path: str):
        class DependencyVisitor(ast.NodeVisitor):
            def __init__(self, rel_path_val: str, nodes_dict: Dict, edges_set: Set):
                self.rel_path = rel_path_val
                self.nodes = nodes_dict
                self.edges = edges_set
                self.scope_stack = []
                self.imports = {}  # local_name -> full_path

            def visit_Import(self, node):
                for alias in node.names:
                    name = alias.asname or alias.name
                    self.imports[name] = alias.name
                    # Edge from file to the imported module
                    self.edges.add((self.rel_path, alias.name, "IMPORTS"))

            def visit_ImportFrom(self, node):
                module = node.module or ""
                for alias in node.names:
                    name = alias.asname or alias.name
                    full_name = f"{module}.{alias.name}" if module else alias.name
                    self.imports[name] = full_name
                    # Edge from file to the specific imported symbol
                    self.edges.add((self.rel_path, full_name, "IMPORTS"))

            def visit_ClassDef(self, node):
                self.scope_stack.append(node.name)
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_FunctionDef(self, node):
                self.scope_stack.append(node.name)
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_AsyncFunctionDef(self, node):
                self.visit_FunctionDef(node)

            def visit_Call(self, node):
                # Identify caller scope
                if self.scope_stack:
                    from_id = f"{self.rel_path}::{'::'.join(self.scope_stack)}"
                else:
                    from_id = self.rel_path

                # Identify callee name
                callee_raw = self._get_name(node.func)
                if callee_raw:
                    resolved = self._resolve_callee(callee_raw)
                    if resolved:
                        self.edges.add((from_id, resolved, "CALLS"))
                
                self.generic_visit(node)

            def _get_name(self, node) -> Optional[str]:
                if isinstance(node, ast.Name):
                    return node.id
                elif isinstance(node, ast.Attribute):
                    obj = self._get_name(node.value)
                    if obj:
                        return f"{obj}.{node.attr}"
                return None

            def _resolve_callee(self, name: str) -> str:
                # 1. Check if it matches an imported name or module
                parts = name.split(".")
                root = parts[0]
                if root in self.imports:
                    module_path = self.imports[root]
                    if len(parts) > 1:
                        return f"{module_path}.{'.'.join(parts[1:])}"
                    return module_path
                
                # 2. Check if it's a local symbol in the current file
                local_id = f"{self.rel_path}::{name}"
                if local_id in self.nodes:
                    return local_id
                
                return name

        DependencyVisitor(rel_path, self.nodes, self.edges).visit(tree)

    def summary(self):
        print(f"\n--- SRM Code Nerve Summary ---")
        print(f"Root: {self.root_path}")
        print(f"Found {len(self.nodes)} Nodes, {len(self.edges)} Edges")
        
        if self.nodes:
            # Pick a function to demonstrate dependency tracing
            funcs = [k for k, v in self.nodes.items() if v['type'] == 'function']
            if funcs:
                # Try to find a function that actually has dependencies
                target = None
                for f in funcs:
                    if any(frm == f for frm, to, typ in self.edges):
                        target = f
                        break
                if not target:
                    target = funcs[0]

                print(f"\nExample Trace: {target}")
                deps = [to for frm, to, typ in self.edges if frm == target]
                if deps:
                    for d in deps:
                        print(f"  -> {d}")
                else:
                    print("  (No outbound edges detected for this symbol)")

if __name__ == "__main__":
    # Point at the current src directory
    current_dir = Path(__file__).parent.resolve()
    builder = ASTGraphBuilder(str(current_dir))
    builder.build()
    builder.summary()
