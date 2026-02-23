from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from llm_orchestrator import LLMOrchestrator
from logger import get_logger

from core.events import SessionID

_logger = get_logger()


@dataclass
class DiscoveryCircle:
    name: str
    files: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ContextEnvelope:
    intent_summary: str
    entities: List[str]
    circles: List[DiscoveryCircle] = field(default_factory=list)
    git_history: str = ""
    summary_text: str = ""
    working_set: List[str] = field(default_factory=list)


class ContextEngine:
    def __init__(self, repo_path: str, edit_rate_limit: float = 0.5):
        self.repo_path = Path(repo_path).resolve()
        self.orchestrator = LLMOrchestrator(repo_path, edit_rate_limit)

    async def build_context_envelope(
        self,
        session_id: SessionID,
        prompt: str,
        additional_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[ContextEnvelope, str]:
        intent = await self._extract_intent(prompt)
        keywords = intent.get("entities", []) or self._fallback_keywords(prompt)
        direct_files = self._resolve_direct_files(keywords)
        functional_files = self._resolve_functional_files(direct_files)
        validation_files = self._resolve_validation_files(direct_files + functional_files, keywords)
        contextual_files = self._resolve_contextual_files()

        intent_summary_text = intent.get("summary", "")
        direct_reason = self._describe_direct_circle(keywords, direct_files, intent_summary_text)
        functional_reason = self._describe_functional_circle(functional_files, direct_files)
        validation_reason = self._describe_validation_circle(validation_files)
        contextual_reason = self._describe_contextual_circle(contextual_files)

        circles = []
        circles.append(DiscoveryCircle(
            name="Circle 1 (Direct)",
            files=direct_files,
            reason=direct_reason,
        ))
        circles.append(DiscoveryCircle(
            name="Circle 2 (Functional)",
            files=functional_files,
            reason=functional_reason,
        ))
        circles.append(DiscoveryCircle(
            name="Circle 3 (Validation)",
            files=validation_files,
            reason=validation_reason,
        ))
        circles.append(DiscoveryCircle(
            name="Circle 4 (Contextual)",
            files=contextual_files,
            reason=contextual_reason,
        ))

        git_history = self._collect_git_history()
        working_set = list({*direct_files, *functional_files, *validation_files, *contextual_files})
        summary_text = self._build_summary(intent_summary_text, circles, git_history)

        envelope = ContextEnvelope(
            intent_summary=intent_summary_text,
            entities=keywords,
            circles=circles,
            git_history=git_history,
            summary_text=summary_text,
            working_set=working_set,
        )
        reason_fragments = [frag for frag in (direct_reason, functional_reason, validation_reason, contextual_reason) if frag]
        reason = " | ".join(reason_fragments) if reason_fragments else "Context discovery completed without explicit matches."
        return envelope, reason

    async def _extract_intent(self, prompt: str) -> Dict[str, Any]:
        plan_prompt = (
            "Analyze the developer prompt and identify the primary intent and key entities. "
            "Return valid JSON with keys 'summary', 'entities', and optional 'focus'. Example:\n"
            "{\"summary\": \"Refactor auth flow\", \"entities\": [\"auth\", \"login\"]}\n\n"
            f"Prompt:\n{prompt}\n\n"
            "JSON:\n"
        )
        try:
            result = await self.orchestrator.run_assistant(plan_prompt, agent="plan")
            if not result or not result.strip():
                raise ValueError("Assistant returned empty intent payload")
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return {
                    "summary": parsed.get("summary", ""),
                    "entities": parsed.get("entities", []),
                    "focus": parsed.get("focus", ""),
                }
        except Exception as exc:  # fallback to heuristics
            _logger.debug(f"Intent extractor failed: {exc}")
        return {
            "summary": self._fallback_summary(prompt),
            "entities": self._fallback_keywords(prompt),
            "focus": "",
        }

    def _fallback_summary(self, prompt: str) -> str:
        keywords = self._fallback_keywords(prompt, max_tokens=3)
        return "Focus on " + ", ".join(keywords) if keywords else prompt[:140]

    def _fallback_keywords(self, prompt: str, max_tokens: int = 5) -> List[str]:
        tokens = [tok.lower() for tok in re.findall(r"[a-zA-Z_]+", prompt) if len(tok) > 3]
        unique = []
        for tok in tokens:
            if tok not in unique:
                unique.append(tok)
            if len(unique) >= max_tokens:
                break
        return unique

    def _resolve_direct_files(self, keywords: Iterable[str]) -> List[str]:
        if not keywords:
            return []

        scan_paths = [self.repo_path / "src", self.repo_path / "tests", self.repo_path]
        lowered = [kw.lower() for kw in keywords if kw]
        candidates: Dict[str, float] = {}
        skip_dirs = {".git", "node_modules", "__pycache__", "venv", ".venv"}
        max_size = 1_000_000

        for base in scan_paths:
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                suffix = path.suffix.lower()
                if suffix in {".pyc", ".pyo"}:
                    continue
                if any(part in skip_dirs for part in path.parts):
                    continue

                try:
                    rel = str(path.relative_to(self.repo_path))
                except ValueError:
                    continue

                try:
                    if path.stat().st_size > max_size:
                        continue
                except OSError:
                    continue

                score = 0.0
                name_lower = path.name.lower()
                for kw in lowered:
                    if kw in name_lower:
                        score += 2.5

                snippet = ""
                try:
                    with path.open("r", encoding="utf-8", errors="ignore") as stream:
                        snippet = stream.read(20000).lower()
                except Exception:
                    continue

                for kw in lowered:
                    if kw and kw in snippet:
                        occurrences = snippet.count(kw)
                        score += min(occurrences, 10)

                if score <= 0:
                    continue

                previous = candidates.get(rel, 0.0)
                if score > previous:
                    candidates[rel] = score

        ordered = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))
        return [rel for rel, _ in ordered[:6]]

    def _resolve_functional_files(self, direct_files: List[str]) -> List[str]:
        imports = set()
        for rel_path in direct_files:
            target = self.repo_path / rel_path
            if not target.exists():
                continue
            try:
                with target.open("r", encoding="utf-8", errors="ignore") as stream:
                    for line in stream:
                        match = re.match(r"^(?:from|import)\s+([a-zA-Z0-9_\.]+)", line)
                        if match:
                            module = match.group(1).split(".")[0]
                            imports.add(module)
            except Exception:
                continue
        return self._match_module_to_files(imports)

    def _match_module_to_files(self, modules: Iterable[str]) -> List[str]:
        results = []
        seen = set()
        for module in modules:
            paths = [self.repo_path / f"{module}.py", self.repo_path / module / "__init__.py"]
            for path in paths:
                if path.exists():
                    rel = str(path.relative_to(self.repo_path))
                    if rel not in seen:
                        results.append(rel)
                        seen.add(rel)
                        if len(results) >= 5:
                            return results
        return results

    def _resolve_validation_files(self, candidates: List[str], keywords: Iterable[str]) -> List[str]:
        tests = []
        seen = set()
        lowered_keywords = [kw.lower() for kw in keywords]
        test_bases = [self.repo_path / "tests", self.repo_path]
        for base in test_bases:
            if not base.exists():
                continue
            for path in base.rglob("test_*.py"):
                rel = str(path.relative_to(self.repo_path))
                if rel in seen:
                    continue
                if any(kw in rel.lower() for kw in lowered_keywords):
                    tests.append(rel)
                    seen.add(rel)
                    if len(tests) >= 4:
                        return tests
            for path in base.rglob("*_test.py"):
                rel = str(path.relative_to(self.repo_path))
                if rel in seen:
                    continue
                if any(kw in rel.lower() for kw in lowered_keywords):
                    tests.append(rel)
                    seen.add(rel)
                    if len(tests) >= 4:
                        return tests
        if not tests and candidates:
            for candidate in candidates:
                candidate_name = Path(candidate).stem
                for path in self.repo_path.rglob(f"test_{candidate_name}*.py"):
                    rel = str(path.relative_to(self.repo_path))
                    if rel not in seen:
                        tests.append(rel)
                        seen.add(rel)
                        if len(tests) >= 4:
                            return tests
        return tests

    def _resolve_contextual_files(self) -> List[str]:
        picks = []
        seen = set()
        docs = list(self.repo_path.glob("README*.md"))
        docs += list((self.repo_path / "docs").rglob("*.md") if (self.repo_path / "docs").exists() else [])
        for doc in docs:
            rel = str(doc.relative_to(self.repo_path))
            if rel not in seen:
                picks.append(rel)
                seen.add(rel)
                if len(picks) >= 3:
                    break
        return picks

    def _collect_git_history(self) -> str:
        try:
            history = subprocess.run(
                ["git", "log", "-5", "--oneline"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=4,
            )
            if history.returncode == 0:
                return history.stdout.strip()
        except Exception as exc:
            _logger.debug(f"Git history capture failed: {exc}")
        return ""

    def _build_summary(self, summary: str, circles: List[DiscoveryCircle], git_history: str) -> str:
        lines = []
        if summary:
            lines.append(f"Intent: {summary}")
        for circle in circles:
            if circle.files:
                excerpt = ", ".join(circle.files[:3])
                lines.append(f"{circle.name}: {excerpt} ({circle.reason})")
        if git_history:
            lines.append("Recent git history:\n" + git_history)
        return "\n".join(lines)

    def _summarize_files_for_reason(self, files: List[str], limit: int = 3) -> str:
        if not files:
            return ""
        excerpt = ", ".join(files[:limit])
        if len(files) > limit:
            excerpt += f", +{len(files) - limit} more"
        return excerpt

    def _keyword_snippet(self, keywords: Iterable[str], summary: str, limit: int = 3) -> str:
        cleaned = [kw for kw in keywords if kw]
        if cleaned:
            return ", ".join(cleaned[:limit])
        fallback = [tok for tok in re.findall(r"[a-zA-Z0-9_]+", summary) if len(tok) > 2]
        if fallback:
            return ", ".join(fallback[:limit])
        return "general focus"

    def _describe_direct_circle(self, keywords: Iterable[str], files: List[str], summary: str) -> str:
        snippet = self._summarize_files_for_reason(files)
        if not snippet:
            return "Direct circle did not surface explicit targets."
        keyword_snip = self._keyword_snippet(keywords, summary)
        return f"Direct circle matched keywords ({keyword_snip}) to {snippet}."

    def _describe_functional_circle(self, files: List[str], direct_files: List[str]) -> str:
        snippet = self._summarize_files_for_reason(files)
        if not snippet:
            return "Functional circle did not add dependency context."
        direct_snip = self._summarize_files_for_reason(direct_files) or "primary targets"
        return f"Functional circle expanded {direct_snip} to {snippet} via imports."

    def _describe_validation_circle(self, files: List[str]) -> str:
        snippet = self._summarize_files_for_reason(files)
        if not snippet:
            return "Validation circle could not identify linked tests."
        return f"Validation circle added {snippet} to keep verification coverage aligned."

    def _describe_contextual_circle(self, files: List[str]) -> str:
        snippet = self._summarize_files_for_reason(files)
        if not snippet:
            return "Contextual circle found no docs or architectural notes."
        return f"Contextual circle captured {snippet} for architectural context."
