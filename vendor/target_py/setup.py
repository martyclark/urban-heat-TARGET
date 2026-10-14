from setuptools import setup

setup(
    name="target_py",
    version="0.1.2",
    # This directory *is* the target_py package, so map it explicitly
    # (find_packages() here would install scripts/ui/example as top-level packages).
    packages=["target_py", "target_py.scripts", "target_py.ui", "target_py.example"],
    package_dir={"target_py": "."},
    install_requires=[
        "matplotlib>=3.5.0",
        "numpy>=1.24.4",
        "pandas>=2.0.3",
        "scipy>=1.10.1",
        "tqdm>=4.54.1",
    ],
)
