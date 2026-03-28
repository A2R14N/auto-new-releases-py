from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
here = Path(__file__).parent
long_description = (here / "README.md").read_text(encoding="utf-8")

setup(
    name="auto-new-releases",
    version="2.0.0",
    description="Automatically track new Spotify releases from your favourite artists and add them to a playlist",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Adrian",
    python_requires=">=3.9",
    packages=find_packages(exclude=["tests*"]),
    install_requires=[
        "spotipy>=2.23.0",
        "requests>=2.28.0",
    ],
    extras_require={
        "rich": ["rich>=13.0.0"],
        "dev": [
            "pytest>=7.0",
            "pytest-cov",
        ],
    },
    entry_points={
        "console_scripts": [
            "auto-new-releases=anr:main",
            "anr=anr:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Multimedia :: Sound/Audio",
    ],
    keywords="spotify music releases playlist automation",
    project_urls={
        "Source": "https://github.com/adrian/auto-new-releases",
    },
)
