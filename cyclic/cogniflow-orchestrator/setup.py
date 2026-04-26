from setuptools import setup, find_packages

setup(
    name="cogniflow-orchestrator",
    version="3.5.0",
    description="Cogniflow Multi-Agent Orchestrator — DAG and cyclic graph pipelines via Claude CLI (GAP-1/2/3 restored, file-based config)",
    packages=find_packages(exclude=["tests*"]),
    package_data={
        "orchestrator": ["hook_scripts/*.py"],
    },
    python_requires=">=3.10",
    install_requires=[
        "networkx>=3.0",
        "filelock>=3.12",
    ],
    extras_require={
        "dev":    ["pytest>=7.0", "pytest-cov>=4.0"],
        "schema": ["jsonschema>=4.0"],
    },
    entry_points={
        "console_scripts": [
            "cogniflow=cli:main",
        ],
    },
)
