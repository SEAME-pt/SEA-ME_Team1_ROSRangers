from glob import glob

from setuptools import find_packages, setup

package_name = "flight_interface"

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
    description="Ground link to the drone: FC telemetry over MSP, and the lifecycle node (watchdog, E-stop)",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "fc_telemetry = flight_interface.fc_telemetry:main",
            "flight_interface = flight_interface.link_node:main",
        ],
    },
)
