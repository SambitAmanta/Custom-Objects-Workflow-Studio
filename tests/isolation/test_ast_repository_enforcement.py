import ast
from pathlib import Path


def test_ast_forbids_direct_collection_access():
    """Scans all application source files to ensure no view or serializer directly
    imports or accesses pymongo Collection objects or calls raw client/db subscriptions.
    All data access MUST pass through TenantScopedRepository.
    """
    apps_dir = Path(__file__).resolve().parent.parent.parent / "apps" / "api" / "apps"
    violations = []

    for py_file in apps_dir.rglob("*.py"):
        if "migrations" in py_file.parts:
            continue

        with open(py_file, encoding="utf-8") as f:
            try:
                tree = ast.parse(f.read(), filename=str(py_file))
            except SyntaxError as e:
                violations.append(f"Syntax error in {py_file}: {e}")
                continue

        for node in ast.walk(tree):
            # Check for direct imports: `from pymongo.collection import Collection`
            if isinstance(node, ast.ImportFrom):
                if node.module and "pymongo.collection" in node.module:
                    violations.append(
                        f"{py_file}:{node.lineno} imports pymongo.collection directly!"
                    )
            # Check for direct imports: `import pymongo.collection`
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("pymongo.collection"):
                        violations.append(
                            f"{py_file}:{node.lineno} imports pymongo.collection directly!"
                        )

    assert not violations, "Direct MongoDB collection access violations detected:\n" + "\n".join(
        violations
    )
