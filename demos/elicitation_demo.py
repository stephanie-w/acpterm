#!/usr/bin/env python
from __future__ import annotations

from pathlib import Path
import sys


# Ensure src/ is on python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from acp.schema import (
    ElicitationBooleanPropertySchema,
    ElicitationIntegerPropertySchema,
    ElicitationMultiSelectPropertySchema,
    ElicitationSchema,
    ElicitationStringPropertySchema,
    EnumOption,
    UntitledMultiSelectItems,
)

from acpterm.elicitation import render_form


def main() -> None:
    # 1. Simple Form Schema
    schema = ElicitationSchema(
        type="object",
        title="Configuration Form",
        description="Please provide your preferences for the agent operation.",
        required=["project_name", "enable_logging", "verbosity"],
        properties={
            "project_name": ElicitationStringPropertySchema(
                type="string",
                title="Project Name",
                description="The name of the new python project",
                min_length=3,
                max_length=20,
                default="my-app",
            ),
            "verbosity": ElicitationIntegerPropertySchema(
                type="integer",
                title="Verbosity Level",
                description="Logging verbosity level from 1 to 5",
                minimum=1,
                maximum=5,
                default=3,
            ),
            "enable_logging": ElicitationBooleanPropertySchema(
                type="boolean",
                title="Enable Verbose Logging",
                description="Should we store debug logs on disk?",
                default=True,
            ),
            "programming_language": ElicitationStringPropertySchema(
                type="string",
                title="Primary Programming Language",
                description="Select the language for templates",
                default="python",
                one_of=[
                    EnumOption(const="python", title="Python 3.14+"),
                    EnumOption(const="rust", title="Rust Lang"),
                    EnumOption(const="typescript", title="TypeScript/Node"),
                ],
            ),
            "tags": ElicitationMultiSelectPropertySchema(
                type="array",
                title="Project Tags",
                description="Select tags to categorize this repository",
                min_items=1,
                max_items=3,
                default=["cli", "backend"],
                items=UntitledMultiSelectItems(
                    type="string",
                    enum=["cli", "backend", "frontend", "library", "docker"],
                ),
            ),
        },
    )

    print("=== TESTING INTERACTIVE FORM RENDERING ===")
    result = render_form(
        message="The agent needs to configure a new environment.",
        schema=schema,
        auto_accept=False,
    )
    print("\n--- Result ---")
    print(result)

    print("\n=== TESTING AUTO_ACCEPT (Defaults) ===")
    result_auto = render_form(
        message="The agent needs to configure a new environment.",
        schema=schema,
        auto_accept=True,
    )
    print("\n--- Auto-accept Result ---")
    print(result_auto)


if __name__ == "__main__":
    main()
