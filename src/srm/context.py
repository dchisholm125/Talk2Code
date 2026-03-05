from pathlib import Path
from typing import List, Dict, Any, Optional
from srm.planner import ASTPlanner
from srm.bridge import SRMCodeBridge
from core.logger import get_logger

_logger = get_logger()

class SRMContextEngine:
    """The Orchestrator for the Symbolic Reasoning Model Context Layer."""
    _instance = None
    
    def __new__(cls, root_path: str):
        if cls._instance is None:
            cls._instance = super(SRMContextEngine, cls).__new__(cls)
            cls._instance.root_path = Path(root_path).resolve()
            cls._instance.planner = ASTPlanner(str(cls._instance.root_path))
            cls._instance.bridge = SRMCodeBridge(str(cls._instance.root_path))
            cls._instance.is_initialized = False
        return cls._instance

    def __init__(self, root_path: str):
        pass

    def boot(self):
        """Initializes the AST Graph and Semantic Brain."""
        if not self.is_initialized:
            self.planner.initialize()
            self.is_initialized = True

    def get_context_for_prompt(self, prompt: str, mode: str = "build") -> str:
        """Runs the SRM pipeline: MCTS -> Extractor -> XML Payload."""
        self.boot()
        
        _logger.info(f"[SRM Brain] Extracting context for: \"{prompt}\"")
        
        # 1. Run MCTS to isolate the exact symbols
        node_ids = self.planner.run_mcts(prompt)
        
        # 2. Extract source and build the XML payload
        xml_context = self.bridge.build_opencode_payload(node_ids, mode=mode)
        
        # 3. Apply the conditional System Prompt
        if mode == "plan":
            system_prompt = (
                "System: You are an architectural planner. Review the provided XML context. "
                "Output a concise plan to fulfill the User Request in a Markdown block. "
                "DO NOT use bash, read, or any file-system tools. Rely ONLY on the provided context."
            )
        else: # Build mode
            system_prompt = (
                "System: You are an autonomous execution agent. The provided XML context contains the "
                "exact files you need to modify. You have FULL PERMISSION to use your tools (read, edit, "
                "write, bash) to execute the User's Request. CRITICAL DIRECTIVE: You operate in zero-shot mode. "
                "DO NOT ask the user for clarification. DO NOT ask for permission to run commands. "
                "DO NOT stop to explain your thought process. Execute the file changes, verify them "
                "if necessary, and terminate your run successfully."
            )
        
        # 4. Final Assembly: System Prompt -> XML Context -> User Request
        full_payload = f"{system_prompt}\n\n{xml_context}\n\nUser Request: {prompt}"
        
        _logger.info(f"[SRM Bridge] Assembled {len(node_ids)} source blocks. Full Payload size: {len(full_payload)} chars.")
        return full_payload

    def sync_file_changes(self, modified_files: List[str]):
        """Incremental update (Synaptic Plasticity) when files are modified."""
        self.boot()
        _logger.info(f"--- SRM Syncing Changes for {len(modified_files)} files ---")
        for filepath in modified_files:
            try:
                self.planner.update_symbols(filepath)
            except Exception as e:
                _logger.warning(f"Failed to sync {filepath}: {e}")

if __name__ == "__main__":
    # Test Synaptic Plasticity
    import os
    engine = SRMContextEngine(".")
    engine.boot()
    
    # Simulate a file modification
    test_file = "src/daemon.py"
    print(f"\n[TESTING INCREMENTAL UPDATE] on {test_file}")
    engine.sync_file_changes([test_file])
    
    print("\n[TESTING CONTEXT EXTRACTION]")
    payload = engine.get_context_for_prompt("How do we handle Telegram messages?")
    print(payload[:500] + "...")
