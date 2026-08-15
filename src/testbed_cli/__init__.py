"""The composition root.

This is the only package allowed to discover plug-ins and wire concrete
adapters, topologies and packs into the kernel. Everything below it depends on
contracts and the Pack SDK alone.
"""
