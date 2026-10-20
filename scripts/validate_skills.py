from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / ".agents" / "skills"


def main() -> None:
    skill_files = sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    if not skill_files:
        raise SystemExit("No repository skills found")
    names: set[str] = set()
    for path in skill_files:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise SystemExit(f"{path}: missing YAML frontmatter")
        frontmatter = text.split("---\n", 2)[1]
        metadata = yaml.safe_load(frontmatter)
        name = metadata.get("name") if isinstance(metadata, dict) else None
        description = metadata.get("description") if isinstance(metadata, dict) else None
        if name != path.parent.name:
            raise SystemExit(f"{path}: name must match its directory")
        if not isinstance(description, str) or not description.strip():
            raise SystemExit(f"{path}: description is required")
        if name in names:
            raise SystemExit(f"{path}: duplicate skill name {name}")
        names.add(name)
        openai_path = path.parent / "agents" / "openai.yaml"
        if openai_path.exists():
            openai = yaml.safe_load(openai_path.read_text(encoding="utf-8"))
            interface = openai.get("interface") if isinstance(openai, dict) else None
            if not isinstance(interface, dict):
                raise SystemExit(f"{openai_path}: interface mapping is required")
            short = interface.get("short_description")
            if not isinstance(short, str) or not 25 <= len(short) <= 64:
                raise SystemExit(f"{openai_path}: short_description must be 25-64 characters")
            prompt = interface.get("default_prompt")
            if not isinstance(prompt, str) or f"${name}" not in prompt:
                raise SystemExit(f"{openai_path}: default_prompt must mention ${name}")
    print(f"Validated {len(skill_files)} repository skill(s).")


if __name__ == "__main__":
    main()
