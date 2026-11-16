from glob import glob

from setuptools import find_packages, setup

package_name = "px4_offboard_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Thiago de Paula",
    maintainer_email="thiagodepaulapt@gmail.com",
    description="SIMULATION ONLY: mission velocity target to PX4 offboard (#86)",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "px4_offboard_bridge = px4_offboard_bridge.bridge_node:main",
            "sim_smoke_scenario = px4_offboard_bridge.smoke_scenario:main",
        ],
    },
)
