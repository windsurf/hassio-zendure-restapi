"""What goes to GitHub: no development references, versions in step, links that work.

Patterns only: real names and serial numbers are not written here, since this
file is public itself. Each check reads the files of the repository as they are.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "custom_components" / "zendure_restapi"
TEXT = (".md", ".py", ".json", ".yaml", ".yml", ".ps1", ".txt")
SELF = Path(__file__).name

# Work codes (a letter and a number), paragraph signs and local paths: none of
# them mean anything to a user.
DEV_CODES = re.compile(r"§\d|\bW\d{1,2}\b|\bG\d{1,2}[ab]?\b|\bB1\d\b|\bM[12]\b|\bO1\b|\bT-S\d")
LOCAL_PATH = re.compile(r"(?:/home|/tmp|/mnt|/Users)/[\w.-]+")
# Something shaped like a device serial: 14 to 16 capitals and digits, both.
SERIAL = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{14,16}\b")
FICTIONAL = ("FAKE", "XX", "SIMULATED", "TEST")
PRIVATE_IP = re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}")
PROMISE = re.compile(r"\bcoming in \d|\bplanned for v?\d|\bin a future (?:release|version)\b", re.IGNORECASE)


def _files():
    for path in sorted(ROOT.rglob("*")):
        parts = set(path.relative_to(ROOT).parts)
        if not path.is_file() or path.suffix not in TEXT or path.name == SELF:
            continue
        if parts & {".git", "__pycache__", ".pytest_cache"}:
            continue
        yield path


def _hits(pattern):
    found = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        found += [f"{path.relative_to(ROOT)}: {m.group(0)}" for m in pattern.finditer(text)]
    return found


def test_no_work_codes_or_paragraph_signs():
    assert _hits(DEV_CODES) == []


def test_no_local_paths():
    assert _hits(LOCAL_PATH) == []


def test_only_fictional_serial_numbers():
    real = [hit for hit in _hits(SERIAL) if not any(mark in hit.rsplit(": ", 1)[1] for mark in FICTIONAL)]
    assert real == []


def test_no_private_addresses_or_email():
    assert _hits(PRIVATE_IP) == []
    assert _hits(EMAIL) == []


def test_no_promises_of_a_version():
    readmes = [p for p in _files() if p.suffix == ".md"]
    found = [f"{p.name}: {m.group(0)}" for p in readmes for m in PROMISE.finditer(p.read_text(encoding="utf-8"))]
    assert found == []


def _version():
    return json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))["version"]


def test_one_version_everywhere():
    version = _version()
    const = (PACKAGE / "const.py").read_text(encoding="utf-8")
    assert f'INTEGRATION_VERSION = "{version}"' in const
    for sample in sorted((ROOT / "dashboard").glob("*.yaml")):
        head = sample.read_text(encoding="utf-8").splitlines()[1]
        assert head.endswith(f" - v{version}"), sample.name


def test_the_changelog_has_this_version():
    text = (ROOT / "README2.md").read_text(encoding="utf-8")
    assert re.search(rf"^### v{re.escape(_version())} ", text, re.MULTILINE)


def _keys(data, prefix=""):
    keys = set()
    for key, value in data.items():
        keys |= _keys(value, f"{prefix}{key}.") if isinstance(value, dict) else {prefix + key}
    return keys


def test_every_language_has_every_text():
    english = _keys(json.loads((PACKAGE / "strings.json").read_text(encoding="utf-8")))
    for path in sorted((PACKAGE / "translations").glob("*.json")):
        keys = _keys(json.loads(path.read_text(encoding="utf-8")))
        assert keys == english, f"{path.name}: missing {sorted(english - keys)[:5]}, extra {sorted(keys - english)[:5]}"


def _slug(heading):
    heading = re.sub(r"<[^>]+>|[*`]", "", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")


def _anchors(path):
    text = path.read_text(encoding="utf-8")
    anchors, seen, fenced = set(re.findall(r'<a id="([^"]+)"', text)), {}, False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        match = None if fenced else re.match(r"#{1,6}\s+(.*)", line)
        if match:
            slug = _slug(match.group(1))
            count = seen.get(slug, 0)
            anchors.add(slug if count == 0 else f"{slug}-{count}")
            seen[slug] = count + 1
    return anchors


def test_every_link_to_a_heading_finds_it():
    documents = sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").glob("*.md"))
    known = {path.resolve(): _anchors(path) for path in documents}
    broken = []
    for path in documents:
        text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        for target, anchor in re.findall(r"\]\(([^)\s#]*)#([^)\s]+)\)", text):
            if target.startswith("http"):
                continue
            goal = (path.parent / target).resolve() if target else path.resolve()
            if goal in known and anchor not in known[goal]:
                broken.append(f"{path.name} -> {target}#{anchor}")
    assert broken == []
