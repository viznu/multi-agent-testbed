"""Adapters: the only place framework, protocol and process specifics live.

An adapter may import contracts and the Pack SDK. It must never import kernel or
eval internals, and never another adapter.
"""
