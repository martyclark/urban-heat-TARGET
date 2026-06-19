from setuptools import setup, find_packages

setup(
    name="target_py",
    version="0.1.2",
    packages=find_packages(),
    install_requires=[
        "matplotlib>=3.5.0",
        "numpy>=1.24.4",
        "pandas>=2.0.3",
        "scipy>=1.10.1",
        "tqdm>=4.54.1",
    ],
)
