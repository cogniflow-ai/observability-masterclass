from setuptools import setup, find_packages

setup(
    name="cogniflow-orchestrator",
    version="1.0.4",
    description="File-based multi-agent DAG orchestrator for the Claude CLI",
    author="Cogniflow AI",
    author_email="giuseppe.basile@cogniflow-ai.com",
    python_requires=">=3.10",
    packages=find_packages(exclude=["tests*"]),
    install_requires=[
        "networkx>=3.0",
        "filelock>=3.12",
    ],
    extras_require={
        "dev": ["pytest>=7.0", "pytest-cov>=4.0"],
    },
    entry_points={
        "console_scripts": [
            "cogniflow=cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
