From Derek (repository owner) directly: Our mantra is: expose all failures ASAP, fail fast, and control reliability with test harnesses. When we move forward we do NOT regress!

# Guiding Principles for Ambient AI Development

This document outlines the core tenets of the Talk2Code / voice-to-code project. These principles ensure the tool remains effective for its primary use case: **remote, high-pressure, mobile-first codebase management.**

## 1. Speed is a Feature
- **Latency is the Enemy**: Every extra second spent waiting at a bus stop or on a couch is a second of friction.
- **Model Choice**: Prioritize high-throughput, low-latency models (e.g., MiniMax M2.5) for default operations.
- **Minimal Reasoning**: When appropriate, use minimal reasoning variants to get to the tool-call faster.

## 2. Speed Through Ingenuity
- **Small-Scale Focus**: Ambient coding is for identifying fixes, analyzing logs, and making surgical changes—not for massive refactors.
- **Smart Compression**: Distill long conversations into the most "actionable" gist before handing them to the coding agent.

## 3. Keep the Human Informed
- **No Black Boxes**: If a process (like compression or thinking) takes more than 1 second, the UI must reflect it.
- **Live Streaming**: Stream *everything*—reasoning, intermediate text, and tool calls. 
- **Wait Tickers**: Use visual wait tickers `[Wait: ###s]` for any residences in a single state over 30 seconds.

## 4. Analysis First
- **The "9:35 PM" Use Case**: The system admin at home needs an analyst, not just a writer. The AI should prioritize finding the *root cause* in logs or git commits before proposing code changes.
- **Throughput over Breadth**: Focus on resolving the immediate crisis with high accuracy.
