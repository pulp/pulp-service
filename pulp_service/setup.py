#!/usr/bin/env python3

from setuptools import find_packages, setup

with open("requirements.txt") as requirements:
    requirements = requirements.readlines()

setup(
    name="pulp-service",
    version="0.1.0",
    description="pulp-service plugin for the Pulp Project",
    long_description="pulp-service plugin for the Pulp Project",
    long_description_content_type="text/markdown",
    license="GPLv2+",
    author="AUTHOR",
    author_email="decko@redhat.com",
    url="https://github.com/pulp/pulp-service",
    python_requires=">=3.9",
    install_requires=requirements,
    extra_require={"ci": []},
    include_package_data=True,
    packages=find_packages(exclude=["tests", "tests.*"]),
    classifiers=[
        "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
        "Operating System :: POSIX :: Linux",
        "Framework :: Django",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    entry_points={"pulpcore.plugin": ["pulp_service = pulp_service:default_app_config"]},
)
