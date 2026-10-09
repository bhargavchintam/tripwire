"""Tripwire AI layer (owner: Sripadha).

ai.quick_check.classify is the one in-process seam the checkpoint imports (master §6);
ai.rules holds the deterministic fallbacks, ai.llm the AkashML/OpenAI client wrapper.
Importing any module here has no side effects: no settings read, no network.
"""
