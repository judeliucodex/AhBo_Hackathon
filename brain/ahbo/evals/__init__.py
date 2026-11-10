"""Eval machinery (§16): suites in `evals/suites/*.yaml`, run on the real Kernel in fake mode by `ahbo eval`.

Modules: `checks` (INV-10 spoken-line checks), `harness` (the full system in-process), `scripted` (LLM stand-ins
for red-team cases), `suites` (YAML loader), `judge` (Sonnet-as-judge, only with real models), `runner`.
"""
