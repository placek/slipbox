"""The skill bundle — one slash command that loads the whole workflow.

hermes resolves a *skill bundle* — a small YAML in `<HERMES_HOME>/skill-bundles/`
— as a slash command that loads several skills into one message. Plugin skills
qualify for it: they are addressable as `slipbox:<name>`, so a bundle can name
them even though plugin skills deliberately stay out of the flat `~/.hermes/skills`
tree and out of the prompt's skill index.

Writing that file is the plugin's job rather than the operator's. The bundle is a
*derivative of the skill set* — it should gain a skill the moment the package
ships one, and lose it the moment a deployment withholds it — and anything a
human has to re-copy after every upgrade drifts. So `register()` refreshes it at
startup from the skills actually registered, which also means a read-only
deployment gets a bundle holding only its read-path skill, not a menu of writes
it cannot perform.

Loading nine skills at once is not the waste it first looks: a bundle is invoked
explicitly, never preloaded, so the cost lands only when someone asks for the
whole workflow by typing the command.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)


def install(skills: list[str]) -> Path | None:
    """Write or refresh the bundle for `skills`. Returns the path, or None.

    Never raises: a plugin that failed to register its tools because a YAML file
    could not be written would be a poor trade.
    """
    if not config.bundle_enabled() or not skills:
        return None
    try:
        directory = _bundles_dir()
        if directory is None:
            logger.debug("slipbox: no hermes home — skipping the skill bundle")
            return None
        chosen = config.bundle_skills() or skills
        # Qualified names: a bundle entry resolves through skill_view(), which
        # reads `plugin:skill` as plugin-provided.
        qualified = [f"{config.PLUGIN_ID}:{name}" for name in chosen]
        content = _render(config.bundle_name(), qualified)

        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{config.bundle_name()}.yaml"
        # Idempotent: rewriting an identical file every startup would churn
        # mtimes that hermes' bundle cache keys on.
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            return path
        path.write_text(content, encoding="utf-8")
        logger.info("slipbox: skill bundle /%s → %d skills (%s)",
                    config.bundle_name(), len(qualified), path)
        return path
    except Exception as exc:  # noqa: BLE001 - a bundle is a convenience, not a dependency
        logger.debug("slipbox: could not write the skill bundle (%s)", exc)
        return None


def _bundles_dir() -> Path | None:
    """Where hermes reads bundles from, for *this* profile.

    `get_hermes_home()` is profile-aware, which is the whole point: two profiles
    over two knowledge bases must not share one bundle. When hermes is absent
    (bare interpreter, the CLI, the tests) there is nothing to write for, and
    guessing `~/.hermes` would plant a bundle in whichever profile happens to
    own that path — so return None and skip instead.
    """
    override = os.environ.get("HERMES_BUNDLES_DIR")
    if override:
        return Path(override).expanduser()
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]

        return Path(get_hermes_home()) / "skill-bundles"
    except Exception:  # noqa: BLE001 - no hermes on the path
        home = os.environ.get("HERMES_HOME", "").strip()
        return Path(home).expanduser() / "skill-bundles" if home else None


def _render(name: str, skills: list[str]) -> str:
    """The bundle YAML. Hand-rolled so the plugin keeps its stdlib-only import.

    The values are ours — a slug and `plugin:skill` identifiers — so the only
    escaping that can matter is the quoting already applied here.
    """
    lines = [
        f"name: {_scalar(name)}",
        f"description: {_scalar(config.bundle_description())}",
        "skills:",
        *(f"  - {_scalar(skill)}" for skill in skills),
    ]
    instruction = config.bundle_instruction().strip()
    if instruction:
        lines.append("instruction: |")
        lines += [f"  {line}" if line else "" for line in instruction.splitlines()]
    return "\n".join(lines) + "\n"


def _scalar(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
