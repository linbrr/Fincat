"""Skill packaging and installation utilities."""

import json
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class SkillPackage:
    """Represents a skill package metadata."""
    package_version: str
    name: str
    version: str
    author: str | None
    description: str
    dependencies: dict
    created_at: str
    tags: list[str]


class SkillPackager:
    """Package skills into .skill files and install them."""

    CURRENT_VERSION = "1.0"

    def __init__(self, workspace: Path, validator=None):
        self.workspace = Path(workspace)
        self.validator = validator
        self._skills_dir = workspace / "skills"

    def package_skill(
        self,
        skill_name: str,
        output_dir: Path | None = None,
        author: str | None = None,
        tags: list[str] | None = None,
    ) -> Path | None:
        """Package a skill into a .skill file (zip archive)."""
        skill_dir = self._skills_dir / skill_name
        if not skill_dir.exists():
            return None

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        # Validate skill before packaging
        if self.validator:
            is_valid, errors = self.validator.validate(skill_md.read_text())
            if not is_valid:
                from loguru import logger
                logger.warning(
                    "[SkillPackager] Skill '{}' validation failed: {}",
                    skill_name, "; ".join(errors)
                )
                return None

        output_dir = output_dir or Path.cwd()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{skill_name}.skill"

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add SKILL.md
            zf.write(skill_md, arcname=f"{skill_name}/SKILL.md")

            # Add metadata.json
            metadata = self._generate_metadata(skill_name, author, tags)
            zf.writestr(
                f"{skill_name}/metadata.json",
                json.dumps(metadata, ensure_ascii=False, indent=2)
            )

            # Add any reference files
            ref_dir = skill_dir / "references"
            if ref_dir.exists():
                for ref_file in ref_dir.rglob("*"):
                    if ref_file.is_file():
                        rel_path = ref_file.relative_to(skill_dir)
                        zf.write(ref_file, arcname=f"{skill_name}/{rel_path}")

            # Add scripts/ and assets/ if they exist
            for subdir in ("scripts", "assets"):
                sub_path = skill_dir / subdir
                if sub_path.exists():
                    for f in sub_path.rglob("*"):
                        if f.is_file():
                            rel_path = f.relative_to(skill_dir)
                            zf.write(f, arcname=f"{skill_name}/{rel_path}")

        return output_path

    def _generate_metadata(
        self,
        skill_name: str,
        author: str | None,
        tags: list[str] | None,
    ) -> dict:
        """Generate metadata.json from skill frontmatter."""
        skill_md_path = self._skills_dir / skill_name / "SKILL.md"
        if not skill_md_path.exists():
            return {}

        import re
        skill_md = skill_md_path.read_text(encoding="utf-8")

        # Parse frontmatter
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', skill_md, re.DOTALL)
        metadata: dict[str, str] = {}
        if fm_match:
            for line in fm_match.group(1).splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    metadata[key.strip()] = val.strip().strip('"\'')

        # Extract dependencies
        deps = {"tools": [], "skills": [], "bins": []}
        if "metadata" in metadata:
            try:
                meta_json = json.loads(metadata["metadata"])
                fincat_meta = meta_json.get("fincat", {})
                requires = fincat_meta.get("requires", {})
                deps["bins"] = requires.get("bins", [])
            except json.JSONDecodeError:
                pass

        return {
            "package_version": self.CURRENT_VERSION,
            "name": metadata.get("name", skill_name),
            "version": metadata.get("version", "1.0.0"),
            "author": author or metadata.get("author"),
            "description": metadata.get("description", ""),
            "dependencies": deps,
            "created_at": metadata.get("created_at", datetime.now().strftime("%Y-%m-%d")),
            "tags": tags or [],
        }

    def install_skill(
        self,
        skill_file: Path,
        validate: bool = True,
        force: bool = False,
    ) -> tuple[bool, str]:
        """
        Install a .skill file to workspace.

        Returns: (success, message)
        """
        skill_file = Path(skill_file).resolve()
        if not skill_file.exists() or skill_file.suffix != ".skill":
            return False, f"Invalid skill file: {skill_file}"

        try:
            with zipfile.ZipFile(skill_file, "r") as zf:
                namelist = zf.namelist()

                # Find skill directory name from zip structure
                skill_dirs = set(p.split("/")[0] for p in namelist if "/" in p)
                if not skill_dirs:
                    return False, "Invalid .skill file: no skill directory found"
                skill_name = list(skill_dirs)[0]

                # Check if skill already exists
                target_dir = self._skills_dir / skill_name
                if target_dir.exists() and not force:
                    return False, f"Skill '{skill_name}' already exists (use --force to overwrite)"

                # Validate SKILL.md content
                skill_md = None
                for name in namelist:
                    if name.endswith("/SKILL.md"):
                        skill_md = zf.read(name).decode("utf-8")
                        break

                if not skill_md:
                    return False, "SKILL.md not found in package"

                if validate and self.validator:
                    is_valid, errors = self.validator.validate(skill_md)
                    if not is_valid:
                        return False, f"Skill validation failed: {', '.join(errors)}"

                # Extract files
                target_dir.mkdir(parents=True, exist_ok=True)
                for name in namelist:
                    if name.endswith("/"):
                        continue
                    target_path = target_dir / name.replace(f"{skill_name}/", "")
                    if "references/" in name or "scripts/" in name or "assets/" in name:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(target_path, "wb") as f:
                        f.write(zf.read(name))

                return True, f"Successfully installed skill '{skill_name}'"

        except zipfile.BadZipFile:
            return False, "Invalid .skill file (not a valid zip)"
        except Exception as e:
            return False, f"Installation failed: {e}"

    def create_bundle(
        self,
        skill_names: list[str],
        output_path: Path,
        author: str | None = None,
    ) -> Path | None:
        """Create a .skill file containing multiple skills."""
        output_path = Path(output_path)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for skill_name in skill_names:
                skill_dir = self._skills_dir / skill_name
                if not skill_dir.exists():
                    continue

                for file_path in skill_dir.rglob("*"):
                    if file_path.is_file():
                        rel_path = file_path.relative_to(skill_dir)
                        zf.write(file_path, arcname=f"bundle/{skill_name}/{rel_path}")

                # Add metadata for each skill
                metadata = self._generate_metadata(skill_name, author, None)
                zf.writestr(
                    f"bundle/{skill_name}/metadata.json",
                    json.dumps(metadata, ensure_ascii=False, indent=2)
                )

        return output_path

    def extract_bundle(
        self,
        bundle_file: Path,
        target_names: list[str] | None = None,  # None = all
        output_dir: Path | None = None,
    ) -> list[str]:
        """Extract skills from a bundle file."""
        output_dir = output_dir or self._skills_dir
        extracted = []

        with zipfile.ZipFile(bundle_file, "r") as zf:
            # Find all skills in bundle
            all_skills = set()
            for name in zf.namelist():
                if name.startswith("bundle/") and "/" in name[8:]:
                    skill_name = name[8:].split("/")[0]
                    all_skills.add(skill_name)

            skills_to_extract = target_names or list(all_skills)

            for skill_name in skills_to_extract:
                if skill_name not in all_skills:
                    continue

                target_dir = output_dir / skill_name
                target_dir.mkdir(parents=True, exist_ok=True)

                for name in zf.namelist():
                    if name.startswith(f"bundle/{skill_name}/"):
                        rel_path = name[len(f"bundle/{skill_name}/"):]
                        if not rel_path:
                            continue
                        target_path = target_dir / rel_path
                        if name.endswith("/"):
                            target_path.mkdir(parents=True, exist_ok=True)
                        else:
                            target_path.parent.mkdir(parents=True, exist_ok=True)
                            with open(target_path, "wb") as f:
                                f.write(zf.read(name))

                extracted.append(skill_name)

        return extracted
