from setuptools import setup, find_packages

setup(
    name="azure-api-tester",
    version="0.1.0",
    description="Automatically test Azure REST APIs from documentation URLs",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "pyyaml>=6.0",
        "rich>=13.0.0",
        "click>=8.1.0",
    ],
    entry_points={
        "console_scripts": [
            "azure-api-tester=azure_api_tester.cli:main",
        ],
    },
)
