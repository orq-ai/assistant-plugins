"""Shared library for the Evaluator Alignment skill (RES-930).

Deliberately exports nothing at package level: `judge` and `model_backend`
pull in evaluatorq / orq, and importing those eagerly can abort the process on
this Windows host (project memory). Import the submodule you need directly,
e.g. `from lib.runner import load_config`.
"""
