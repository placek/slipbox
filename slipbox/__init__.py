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

from . import atomizer, bundle, commands, config, hooks, schemas, tools

logger = logging.getLogger(__name__)

__version__ = "0.3.0"

TOOLSET = "slipbox"

# `slipbox:persist` is an accepted alias of `slipbox:link` (whitepaper §"The agent").
SKILL_ALIASES = {"link": ("persist",)}

# The only skill that neither writes nor commits — the read path. In a read-only
# deployment (`SLIPBOX_READONLY`) it is the sole skill registered.
READONLY_SKILLS = ("search",)


def _active_schemas() -> tuple[dict, ...]:
    """The tool schemas this deployment advertises.

    The interactive read path (`slipbox_search`, `slipbox_quote`) is registered
    only when the semantic layer is enabled — the whitepaper reserves the
    cited-summary channel for when the models are reachable. A read-only
    deployment (`SLIPBOX_READONLY`) drops the whole write group.
    """
    gated = schemas.GATED if config.semantic_enabled() else ()
    if config.readonly():
        return (*schemas.READ_ONLY, *gated)
    return (*schemas.READ_ONLY, *gated, *schemas.WRITING)


def register(ctx) -> None:
    """Called once at startup: bind tools, commands, skills and hooks.

    Everything runs in-process inside the host agent: the tools, the deterministic
    CARP mechanics and the semantic models all live in this package, loaded into
    hermes directly (no external server, no separate transport).
    """
    # The dedicated atomiser's `host` backend calls the model through hermes'
    # own plugin LLM lane. Bind it once here — it is the only moment the plugin
    # is handed the context, and both the tools and the CLI (cron) reach the
    # agent through the same module afterwards.
    atomizer.bind_host(ctx)

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

    read_only = config.readonly()
    skills = 0
    registered_skills: list[str] = []
    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if not (child.is_dir() and skill_md.exists()):
                continue
            if read_only and child.name not in READONLY_SKILLS:
                continue  # a read-only agent gets only the read-path skill(s)
            ctx.register_skill(child.name, skill_md)
            registered_skills.append(child.name)
            skills += 1
            for alias in SKILL_ALIASES.get(child.name, ()):
                try:
                    ctx.register_skill(alias, skill_md)
                except Exception as exc:  # noqa: BLE001 - aliases are optional
                    logger.debug("slipbox: alias %s not registered (%s)", alias, exc)

    # The bundle is a derivative of what was just registered, so it is written
    # here rather than installed by hand: a read-only deployment gets a bundle
    # holding only its read-path skill, and a new skill joins on next start.
    bundled = bundle.install(registered_skills)

    logger.info(
        "Plugin slipbox %s%s: %d tools, %d commands, %d skills%s.",
        __version__, " (read-only)" if read_only else "",
        registered, len(commands.COMMANDS), skills,
        f", bundle /{config.bundle_name()}" if bundled else "",
    )
