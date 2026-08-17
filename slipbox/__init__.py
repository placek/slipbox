"""Plugin `slipbox` — a curated, agent-operated knowledge base for hermes-agent.

Layers, by *who does the reasoning*:

- `tools.py` / `schemas.py` — deterministic CARP mechanics the agent calls,
- `skills/` — interactive LLM flows (capture → adapt → review → link → search),
- `commands.py` — mechanical slash commands, the morning digest and the CLI,
- `operations.py` — what the tools and commands actually do,
- `lookup.py` — the one four-layer lookup mechanism every skill shares,
- `models.py` — the in-process semantic models (embedder, reranker, judge),
- `embeddings.py` / `folgezettel.py` / `indexmd.py` / `notes.py` — the substrate.

See `../slipbox-whitepaper.typ` for the design this implements.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import commands, config, hooks, schemas, tools

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

TOOLSET = "slipbox"

# `slipbox:persist` is an accepted alias of `slipbox:link` (whitepaper §"The agent").
SKILL_ALIASES = {"link": ("persist",)}


def _active_schemas() -> tuple[dict, ...]:
    """The tool schemas this deployment advertises.

    The interactive read path (`slipbox_search`, `slipbox_quote`) is registered
    only when the semantic layer is enabled — the whitepaper reserves the
    cited-summary channel for when the models are reachable.
    """
    gated = schemas.GATED if config.semantic_enabled() else ()
    return (*schemas.READ_ONLY, *gated, *schemas.WRITING)


def register(ctx) -> None:
    """Called once at startup: bind tools, commands, skills and hooks.

    Everything runs in-process inside the host agent: the tools, the deterministic
    CARP mechanics and the semantic models all live in this package, loaded into
    hermes directly (no external server, no separate transport).
    """
    registered = 0
    for schema in _active_schemas():
        handler = tools.HANDLERS.get(schema["name"])
        if handler is None:  # pragma: no cover - guards a typo in the tables
            logger.warning("slipbox: no handler for %s", schema["name"])
            continue
        ctx.register_tool(
            name=schema["name"], toolset=TOOLSET, schema=schema, handler=handler
        )
        registered += 1

    for name, handler, description in commands.COMMANDS:
        ctx.register_command(name, handler=handler, description=description)

    ctx.register_cli_command(
        name="slipbox",
        help="Operate the slipbox knowledge base (terminal)",
        setup_fn=commands.setup_argparse,
        handler_fn=commands._cli_handler,
    )

    for event, handler in (("on_session_start", hooks.on_session_start),
                           ("on_session_end", hooks.on_session_end)):
        try:
            ctx.register_hook(event, handler)
        except Exception as exc:  # noqa: BLE001 - an unsupported hook is not fatal
            logger.debug("slipbox: hook %s not registered (%s)", event, exc)

    skills = 0
    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if not (child.is_dir() and skill_md.exists()):
                continue
            ctx.register_skill(child.name, skill_md)
            skills += 1
            for alias in SKILL_ALIASES.get(child.name, ()):
                try:
                    ctx.register_skill(alias, skill_md)
                except Exception as exc:  # noqa: BLE001 - aliases are optional
                    logger.debug("slipbox: alias %s not registered (%s)", alias, exc)

    logger.info(
        "Plugin slipbox %s: %d tools, %d commands, %d skills.",
        __version__, registered, len(commands.COMMANDS), skills,
    )
