try:
    import torch
    print("torch is installed")
except ImportError:
    print("torch is MISSING")

try:
    import sentence_transformers
    print("sentence_transformers is installed")
except ImportError:
    print("sentence_transformers is MISSING")
