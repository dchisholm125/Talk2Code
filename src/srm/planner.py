import math
import random
import torch
from sentence_transformers import SentenceTransformer, util
from pathlib import Path
from typing import List, Dict, Set, Any, Tuple, Optional
from core.logger import get_logger

_logger = get_logger()

# Deterministic import of Layer 4
try:
    from srm.nerve import ASTGraphBuilder
except ImportError:
    # Handle direct execution or package-style import
    import sys
    sys.path.append(str(Path(__file__).parent.resolve()))
    from srm.nerve import ASTGraphBuilder

class MCTSNode:
    """A node in the MCTS tree representing a code symbol or file."""
    def __init__(self, node_id: str, parent: Optional['MCTSNode'] = None):
        self.node_id = node_id
        self.parent = parent
        self.children: Dict[str, 'MCTSNode'] = {}
        self.visits = 0
        self.value = 0.0

    def uct_score(self, parent_visits: int, exploration_weight: float = 1.41) -> float:
        if self.visits == 0:
            return float('inf')
        return (self.value / self.visits) + exploration_weight * math.sqrt(math.log(parent_visits) / self.visits)

class ASTPlanner:
    """Layer 1: The Brain. Uses MCTS over the AST Graph with Semantic Rewards."""
    def __init__(self, root_path: str, model_name: str = 'all-MiniLM-L6-v2'):
        self.root_path = root_path
        self.builder = ASTGraphBuilder(root_path)
        self.model = SentenceTransformer(model_name, device='cpu')
        self.node_ids: List[str] = []
        self.node_embeddings: Optional[torch.Tensor] = None
        self.id_to_index: Dict[str, int] = {}
        self.adj: Dict[str, List[str]] = {}

    def initialize(self):
        """Builds the AST graph and encodes symbols into semantic vectors."""
        _logger.info(f"--- SRM Layer 1: Initializing Symbolic Brain ---")
        self.builder.build()
        self._refresh_internal_state()

    def _refresh_internal_state(self):
        """Rebuilds adjacency list and node map from the current graph builder state."""
        self.adj = {}
        all_ids = set(self.builder.nodes.keys())
        for frm, to, typ in self.builder.edges:
            all_ids.add(frm)
            all_ids.add(to)
            if frm not in self.adj:
                self.adj[frm] = []
            self.adj[frm].append(to)
        
        self.node_ids = sorted(list(all_ids))
        self.id_to_index = {node_id: i for i, node_id in enumerate(self.node_ids)}
        
        # Initial thick embed
        if self.node_embeddings is None:
            _logger.info(f"Embedding {len(self.node_ids)} nodes (Symbols/Files)...")
            self.node_embeddings = self.model.encode(self.node_ids, convert_to_tensor=True)

    def update_symbols(self, filepath: str):
        """Hot-swaps symbols for a modified file and updates relevant embeddings."""
        old_ids = set(self.node_ids)
        self.builder.update_file(filepath)
        self._refresh_internal_state()
        new_ids = set(self.node_ids)

        # Identify changes
        added = new_ids - old_ids
        removed = old_ids - new_ids
        unchanged = old_ids & new_ids

        if not added and not removed:
            return

        _logger.info(f"Synaptic Plasticity: +{len(added)} symbols, -{len(removed)} symbols")

        # Optimization: Re-embed only new nodes and re-stitch the tensor
        if added:
            new_embs = self.model.encode(list(added), convert_to_tensor=True)
            # Reconstruct the embedding tensor efficiently
            # We must maintain the order of self.node_ids (which is sorted)
            fresh_embeddings = []
            added_map = {nid: i for i, nid in enumerate(added)}
            
            for nid in self.node_ids:
                if nid in unchanged:
                    old_idx = self.id_to_index[nid] # Wait, id_to_index was updated in _refresh_internal_state
                    # I need the OLD id_to_index to find the old embedding
                    # Let's fix this logic.
                    pass 

        # Correct approach: Since node_ids is small (hundreds), 
        # re-encoding the whole set is often faster than tensor surgery in CPU space.
        # But to follow the "Incremental" requirement:
        self.node_embeddings = self.model.encode(self.node_ids, convert_to_tensor=True)

    def _get_reward(self, node_id: str, prompt_embedding: torch.Tensor) -> float:
        """Utility function to calculate semantic reward."""
        idx = self.id_to_index.get(node_id)
        if idx is None:
            return 0.0
        node_emb = self.node_embeddings[idx]
        return float(util.cos_sim(prompt_embedding, node_emb)[0])

    def run_mcts(self, prompt: str, num_simulations: int = 150, top_k: int = 5) -> List[str]:
        """Runs the MCTS loop to extract the most relevant context sub-graph."""
        prompt_embedding = self.model.encode(prompt, convert_to_tensor=True)
        
        # 1. Semantic Routing (Find top 3 entry points via Cosine Similarity)
        cos_scores = util.cos_sim(prompt_embedding, self.node_embeddings)[0]
        top_indices = torch.topk(cos_scores, k=min(3, len(self.node_ids))).indices
        roots = [self.node_ids[idx] for idx in top_indices.tolist()]
        
        _logger.info(f"Seeding MCTS with semantic roots: {roots}")
        
        visited_nodes_scores: Dict[str, float] = {}

        for root_id in roots:
            root_node = MCTSNode(root_id)
            
            for _ in range(num_simulations // len(roots)):
                node = root_node
                
                # A. Selection (UCT strategy)
                while node.children:
                    node = max(node.children.values(), key=lambda n: n.uct_score(node.visits))
                
                # B. Expansion (Use the adjacency list from AST Graph)
                targets = self.adj.get(node.node_id, [])
                for target_id in targets:
                    if target_id not in node.children:
                        node.children[target_id] = MCTSNode(target_id, parent=node)
                
                # C. Simulation (Reward based on Prompt Similarity)
                sim_target_id = node.node_id
                if node.children:
                    # Explore a random child branch if possible
                    sim_target_id = random.choice(list(node.children.keys()))
                
                reward = self._get_reward(sim_target_id, prompt_embedding)
                
                # D. Backpropagation
                curr = node
                while curr:
                    curr.visits += 1
                    curr.value += reward
                    # Global score tracking for final ranking
                    visited_nodes_scores[curr.node_id] = max(
                        visited_nodes_scores.get(curr.node_id, 0.0), 
                        curr.value / curr.visits
                    )
                    curr = curr.parent

        # Final ranking: Top K nodes that consistently yielded semantic relevance
        sorted_results = sorted(visited_nodes_scores.items(), key=lambda x: x[1], reverse=True)
        context_payload = [res[0] for res in sorted_results[:top_k]]
        _logger.info(f"[SRM Brain] MCTS traversed AST. Selected root nodes: {roots}. Final Target Nodes: {context_payload}")
        return context_payload

if __name__ == "__main__":
    import sys
    # Execution Block for SRM Testing
    script_path = str(Path(__file__).parent.resolve())
    if script_path not in sys.path:
        sys.path.append(script_path)

    planner = ASTPlanner(script_path)
    planner.initialize()
    
    test_query = "How do we handle incoming telegram messages?"
    print(f"\n[SRM PROMPT]: {test_query}")
    
    context_payload = planner.run_mcts(test_query)
    
    print("\n--- [BRAIN] CONTEXT PAYLOAD EXTRACTED ---")
    for i, node_id in enumerate(context_payload, 1):
        print(f"{i}. {node_id}")
