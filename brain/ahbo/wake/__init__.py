"""「喂阿寶」 wake word, harness side (PRD §20.4): the Cantonese sound matcher (Stage 3 twin of the iPad's
Stage 2 `JyutpingMatcher.swift`) and the generator of the shared homophone table (§25.6)."""
from .matcher import WakeMatch, WakeMatcher, stage3_check

__all__ = ["WakeMatch", "WakeMatcher", "stage3_check"]
