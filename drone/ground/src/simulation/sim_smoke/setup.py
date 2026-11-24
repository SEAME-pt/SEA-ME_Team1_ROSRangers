from setuptools import find_packages, setup

package_name = "sim_smoke"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Bruno",
    maintainer_email="bmmc136@gmail.com",
    description="SIMULATION ONLY: make sim-smoke's scenario and checks (#86, #199)",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "sim_smoke_scenario = sim_smoke.smoke_scenario:main",
        ],
    },
)
