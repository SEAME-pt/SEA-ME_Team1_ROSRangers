from glob import glob

from setuptools import find_packages, setup

package_name = "mission_node"

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
    maintainer="Bruno",
    maintainer_email="bmmc136@gmail.com",
    description="Mission node: /gesture/intent -> /mission/velocity_target",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "mission_node = mission_node.mission_node:main",
            "fake_flight_state = mission_node.fake_flight_state:main",
            "fake_intent = mission_node.fake_intent:main",
        ],
    },
)
