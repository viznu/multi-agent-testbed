"""Orchestration policies built entirely from World primitives.

A topology proposes recipients, ordering hints and delays. World validates and
commits. Adding supervisor, debate, market or blackboard behaviour is therefore
a plug-in, never a change to kernel scheduling.
"""
