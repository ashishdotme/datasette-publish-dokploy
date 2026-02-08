from setuptools import setup
import os

VERSION = "0.1a0"


def get_long_description():
    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md"),
        encoding="utf8",
    ) as fp:
        return fp.read()


setup(
    name="datasette-publish-dokploy",
    description="Datasette plugin for publishing data using Dokploy",
    long_description=get_long_description(),
    long_description_content_type="text/markdown",
    author="Ashish",
    url="https://github.com/ashishdotme/datasette-publish-dokploy",
    project_urls={
        "Issues": "https://github.com/ashishdotme/datasette-publish-dokploy/issues",
    },
    license="Apache License, Version 2.0",
    version=VERSION,
    packages=["datasette_publish_dokploy"],
    entry_points={"datasette": ["publish_dokploy = datasette_publish_dokploy"]},
    install_requires=["datasette>=0.59"],
    extras_require={"test": ["pytest"]},
    tests_require=["datasette-publish-dokploy[test]"],
)
