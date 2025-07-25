# setup.py
from pathlib import Path
from setuptools import setup, find_packages


def read_requirements(file: str):
    reqs = []
    for line in Path(file).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            reqs.append(line)
    return reqs


setup(
    name="Oceans",
    version="1.0.0",
    packages=find_packages(include=["oceans", "*"]),
    python_requires=">=3.9",                 
    install_requires=read_requirements("requirements.txt"),
    entry_points={
        "console_scripts": [
            "run_miner=scripts.run_miner:main",
            "run_validator=scripts.run_validator:main",
        ],
    },
)
