from pathlib import Path
from typing import List, Dict, Any, Optional
from srm_ast_mcts import ASTPlanner
from srm_code_bridge import SRMCodeBridge
from logger import get_logger

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
        xml_string = self.bridge.build_opencode_payload(prompt, node_ids, mode=mode)
        
        _logger.info(f"[SRM Bridge] Assembled {len(node_ids)} source blocks. Payload size: {len(xml_string)} chars.")
        return xml_string

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
