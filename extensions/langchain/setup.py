#!/usr/bin/env python3
"""setup.py —— 乾坤镜 LangChain 探针打包"""

import os
from distutils.sysconfig import get_python_lib
from setuptools import setup, find_packages

HERE = os.path.dirname(os.path.abspath(__file__))

setup(
    name="ming-probe-langchain",
    version="0.11.12.post8",
    description="乾坤镜 LangChain 自动探针 —— pip install 即插即用，零代码侵入",
    long_description=open(os.path.join(HERE, "README.md"), encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    license="BSL 1.1",
    author="乾坤镜 team",
    python_requires=">=3.10",
    packages=find_packages(),
    include_package_data=True,
    data_files=[
        (get_python_lib(), ["ming_probe_langchain.pth"]),
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: Other/Proprietary License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    keywords=["langchain", "observability", "diagnostic", "mingjing", "乾坤镜"],
    project_urls={
        "Homepage": "https://github.com/mingjing-probe/ming-probe-langchain",
        "Source": "https://github.com/mingjing-probe/ming-probe-langchain",
    },
)
