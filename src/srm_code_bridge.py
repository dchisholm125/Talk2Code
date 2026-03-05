import ast
from pathlib import Path
from typing import List, Optional, Any

class SRMCodeBridge:
    """Layer 2: The Bridge. Extracts specific source segments for the SRM context payload."""
    
    def __init__(self, root_path: str):
        self.root_path = Path(root_path).resolve()

    def extract_source(self, node_id: str, mode: str = "build") -> str:
        """Parses node_id and extracts the corresponding code segment from the filesystem."""
        parts = node_id.split("::")
        file_rel_path = parts[0]
        symbol_path = parts[1:]

        file_path = self.root_path / file_rel_path
        if not file_path.exists():
            return f"# Warning: File not found: {file_rel_path}"

        try:
            source = file_path.read_text(encoding='utf-8')
            tree = ast.parse(source)
            
            target_node = self._find_node(tree, symbol_path)
            if not target_node:
                return f"# Warning: Symbol {symbol_path} not found in {file_rel_path}"

            if mode == "plan":
                return self._extract_plan_view(source, target_node)
            
            segment = ast.get_source_segment(source, target_node)
            return segment if segment else "# Warning: Could not extract source segment"
            
        except Exception as e:
            return f"# Warning: Error processing {file_rel_path}: {e}"

    def _find_node(self, tree: ast.AST, symbol_path: List[str]) -> Optional[ast.AST]:
        """Traverses the AST to find the node matching the scoped symbol path."""
        current_node = tree
        for sym in symbol_path:
            match = None
            for child in ast.iter_child_nodes(current_node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == sym:
                    match = child
                    break
                elif isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Name) and target.id == sym:
                            match = child
                            break
                    if match: break
            if not match: return None
            current_node = match
        return current_node

    def _extract_plan_view(self, source: str, node: ast.AST) -> str:
        """Extracts only signatures and docstrings, stripping internal implementation."""
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            segment = ast.get_source_segment(source, node)
            return segment if segment else ""

        full_source = ast.get_source_segment(source, node)
        if not full_source: return ""
        lines = full_source.splitlines()
        
        # We want to keep everything up to the first non-docstring statement in the body
        body = getattr(node, "body", [])
        if not body: return full_source
        
        first_statement = body[0]
        # If it's a docstring, we include it and look at the next statement to find the cut point
        if isinstance(first_statement, ast.Expr) and isinstance(first_statement.value, (ast.Constant, ast.Str)):
            if len(body) > 1:
                first_statement = body[1]
            else:
                return full_source # Only a docstring exists

        # Calculate limit based on line numbers
        cut_line_index = first_statement.lineno - node.lineno
        plan_lines = lines[:cut_line_index]
        
        # Prettify the truncated view
        return "\n".join(plan_lines).rstrip() + "\n    ..."

    def build_opencode_payload(self, user_prompt: str, node_ids: List[str], mode: str = "build") -> str:
        """Constructs the XML-tagged payload for the cloud LLM."""
        payload = [
            f"<user_request>{user_prompt}</user_request>",
            "<context_blocks>"
        ]
        
        for node_id in node_ids:
            source = self.extract_source(node_id, mode)
            file_path = node_id.split("::")[0]
            symbol = "::".join(node_id.split("::")[1:])
            
            block = (
                f'  <file path="{file_path}" symbol="{symbol}">\n'
                f'{source}\n'
                f'  </file>'
            )
            payload.append(block)
            
        payload.append("</context_blocks>")
        return "\n".join(payload)

if __name__ == "__main__":
    # Test Block: Self-reflection on the daemon implementation
    import sys
    script_dir = Path(__file__).parent.resolve()
    # Assume we are in src/ or the root
    bridge = SRMCodeBridge(str(script_dir.parent))
    test_node = "src/daemon.py::handle_message"
    prompt = "How do we handle incoming telegram messages?"
    
    print("--- SRM BRIDGE: PLAN MODE (Architecture) ---")
    print(bridge.build_opencode_payload(prompt, [test_node], mode="plan"))
    
    print("\n--- SRM BRIDGE: BUILD MODE (Implementation) ---")
    full_payload = bridge.build_opencode_payload(prompt, [test_node], mode="build")
    # Truncate implementation for visibility
    lines = full_payload.splitlines()
    if len(lines) > 25:
        print("\n".join(lines[:25]) + "\n...")
    else:
        print(full_payload)
